import asyncio
import logging
import re

from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import PageResponse
from app.database import SessionLocal
from app.models.equipment import Equipment
from app.models.lab import Lab
from app.models.reservation import Reservation
from app.models.user import User
from app.schemas.reservation import ReservationCreateRequest, ReservationResponse


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _normalize_time(value: str, field: str = "时间") -> str:
    """把 "9:00" / "09:00" 统一成 "09:00"，非法格式抛业务异常。

    库里 start_time / end_time 存的是字符串，只有补零到两位，
    字符串比较才等价于时间比较（"9:00" > "10:00" 这种坑要在这里堵死）。
    """
    match = _TIME_RE.match((value or "").strip())
    if not match:
        raise BusinessException(message=f"{field}格式应为 HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise BusinessException(message=f"{field}超出合法范围")
    return f"{hour:02d}:{minute:02d}"


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_date(value: str) -> str:
    """日期必须是 YYYY-MM-DD 且真实存在的日历日期。

    库里 date 存字符串、比较全靠字符串序：形状不对的日期
    （"2026-1-1" 会被字符串比较误判成过去，"2026-13-45" 会被当未来落库）
    必须在这里挡死。AI 工具层只查形状（tools.py 的 len==10），真校验在这层。
    """
    text = (value or "").strip()
    if not _DATE_RE.match(text):
        raise BusinessException(message="日期格式应为 YYYY-MM-DD")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise BusinessException(message="日期不是有效的日历日期") from exc
    return text


def create_reservation(db: Session, current_user: User, data: ReservationCreateRequest):
    """创建预约记录。

    并发安全：先对实验室行加悲观锁（SELECT ... FOR UPDATE），把
    "查冲突 → 插入" 串成临界区，同一实验室的并发请求在此排队；
    数据库唯一索引只做兜底，防住绕过本函数直接写库的完全重复。
    """
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    # ── 1. 纯参数校验：不碰数据库，避免无谓地持锁 ──
    start_time = _normalize_time(data.start_time, "开始时间")
    end_time = _normalize_time(data.end_time, "结束时间")
    book_date = _normalize_date(data.date)
    if book_date < current_date:
        raise BusinessException(message="预约日期不能小于当前的日期")
    if end_time <= start_time:
        raise BusinessException(message="预约的结束时间必须晚于开始时间")
    if book_date == current_date and start_time < current_time:
        raise BusinessException(message="预约开始时间不能小于当前的时间")

    # ── 2. 锁住实验室这一行：主键等值查询 → 精确行锁，不会间隙锁膨胀 ──
    #    从这里到 commit 之间，同一 lab_id 的其他预约请求会阻塞排队
    lab = (
        db.query(Lab).filter(Lab.id == data.lab_id).with_for_update().first()
    )
    if not lab:
        raise BusinessException(message="实验室不存在")
    if lab.status != 1:
        raise BusinessException(message="实验室已关闭")
    if lab.open_time and start_time < lab.open_time:
        raise BusinessException(message="预约时间不能早于实验室的开放时间")
    if lab.close_time and end_time > lab.close_time:
        raise BusinessException(message="预约时间不能晚于实验室的关闭时间")

    # ── 容量校验（2026-10-25 补） ──
    # 原来这里没有容量校验：AI 对话层拦了（`app/ai/tools.py`），
    # 但 REST 接口直接走 service，绕过了工具层 → 999 人能约进 30 人的实验室。
    # 而且 AI 层收集的人数传给 service 时被 Pydantic 丢掉（schema 没这字段），
    # 等于人数只是个摆设。现在 schema 加了 people（可选，不落库）。
    # people 为 None 表示调用方没传（前端目前就不传），此时跳过校验，保持向后兼容。
    if data.people is not None:
        if data.people < 1:
            raise BusinessException(message="预约人数至少 1 人")
        capacity = lab.capacity or 0
        if capacity and data.people > capacity:
            raise BusinessException(
                message=f"{lab.name}最多容纳 {capacity} 人，你预约了 {data.people} 人"
            )

    # ── 3. 锁内校验：此刻读到的数据，到 commit 前不会被并发请求改变 ──
    # equipment_id 不为空 则表示这次是预约的实验室设备
    if data.equipment_id:
        equipment = (
            db.query(Equipment).filter(Equipment.id == data.equipment_id).first()
        )
        if not equipment:
            raise BusinessException(message="实验室设备不存在")
        if equipment.status != 1:
            raise BusinessException(message="实验室设备正在维修")
        if equipment.lab_id != data.lab_id:
            # 2026-11-03 补：原来只查存在+状态，REST 能把 B 实验室的设备挂到
            # A 实验室名下预约，跨 lab 的设备占用互不可见 → 同设备可双订。
            # AI 工具层一直有这层（tools.py），这里补齐成同一道防线。
            raise BusinessException(message="设备不属于该实验室")

    # 区间重叠判定：已有 [start, end) 与新的 [start, end) 相交
    # 写成 start < new_end AND end > new_start，覆盖"包含/被包含/左交/右交"四种情况
    # 占用语义（2026-11-03 统一，与 ai/repo.is_available 同口径）：
    #   整室预约（equipment_id 为空）→ 与该实验室任何在用记录冲突
    #   设备预约 → 与整室记录 + 同设备记录冲突；不同设备互不冲突
    query = db.query(Reservation).filter(
        Reservation.lab_id == data.lab_id,
        Reservation.date == book_date,
        Reservation.status.in_([0, 1]),  # 0表示待审核  1已通过
        Reservation.start_time < end_time,
        Reservation.end_time > start_time,
    )
    if data.equipment_id:
        query = query.filter(
            or_(
                Reservation.equipment_id.is_(None),  # 整室被占，设备也用不了
                Reservation.equipment_id == data.equipment_id,  # 同设备被占
            )
        )
    # ★ 重叠查询必须是锁定读（FOR UPDATE），不能是普通一致性读（2026-11-03 压测抓到的真 bug）：
    #   RR 隔离级别下快照在事务第一条读（鉴权依赖查 User）就建立了——早于并发赢家的 commit；
    #   普通 SELECT 读旧快照看不见刚提交的冲突行 → 放行 → 重复落库（唯一索引又因 NULL 不拦）。
    #   实测：8 线程×15 轮出现 5 条完全相同的整室预约；改锁定读后恰好 1 成功 119 拒绝。
    #   锁定读读最新提交数据、绕过快照，配合 Lab 行锁才是完整防线。
    res = query.with_for_update().first()
    if res:
        raise BusinessException(message="该时段已预约")

    # ── 4. 插入：唯一索引冲突说明有并发写入漏网，转成业务提示 ──
    resvervation_model = Reservation(
        user_id=current_user.id,
        lab_id=data.lab_id,
        equipment_id=data.equipment_id,
        date=book_date,
        start_time=start_time,
        end_time=end_time,
        remark=data.remark,
        status=0,
    )
    db.add(resvervation_model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BusinessException(message="该时段刚被占用，请重新选择")


def get_reservation_page_list(
    db: Session,
    current_user: User,
    page: int,
    page_size: int,
    status: int | None = None,
):
    """查询预约的记录"""
    query = db.query(Reservation)
    if current_user.role != "admin":
        query = query.filter(Reservation.user_id == current_user.id)
    if status is not None:
        query = query.filter(Reservation.status == status)
    total = query.count()
    items = (
        query.order_by(Reservation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    for item in items:
        res = ReservationResponse.model_validate(item)
        res.user_name = item.user.name if item.user else None
        res.lab_name = item.lab.name if item.lab else None
        res.equipment_name = item.equipment.name if item.equipment else None
        res.type = "设备" if item.equipment_id else "实验室"
        result.append(res)

    return PageResponse(list=result, total=total)


def cancel_reservation(db: Session, current_user: User, reservation_id: int):
    """学生取消预约"""
    item = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not item:
        raise BusinessException(message="预约记录不存在")
    if item.user_id != current_user.id:
        raise BusinessException(message="无权限", code=403)
    # 1. 只有 待审核(0) 和 已通过(1) 允许取消，已拒绝(2)或已取消(3)直接拦截
    if item.status not in [0,1]:
        raise BusinessException(message="当前状态无法取消")
    # 2. 如果是【审核后取消 (status == 1)】，必须检查实验是否已经开始
    if item.status == 1:
        now = datetime.now()
        currentdate = now.strftime("%Y-%m-%d")
        currenttime = now.strftime("%H:%M")
        if item.date<currentdate or (item.date==currentdate and item.start_time<currenttime):
            raise BusinessException(
                message="实验已开始或已结束，审核通过的预约无法取消"
            )
    # 条件原子更新（2026-11-03）：转移守卫放进 WHERE——读到旧状态与提交之间
    # 若被 audit/expire 并发改过，这里影响行数为 0，报「当前状态无法取消」，
    # 不再把别人转移后的状态覆盖掉。影响行数为 0 时不 commit（请求结束即回滚）。
    updated = (
        db.query(Reservation)
        .filter(
            Reservation.id == reservation_id,
            Reservation.status == item.status,
        )
        .update({"status": 3}, synchronize_session=False)
    )
    if not updated:
        raise BusinessException(message="当前状态无法取消")
    db.commit()


def audit_reservation(db: Session, reservation_id: int, status: int):
    """管理员审核预约"""
    if status not in [1, 2]:
        raise BusinessException(message="审核状态错误")
    item = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not item:
        raise BusinessException(message="预约记录不存在")
    # 条件原子更新（2026-11-03）：WHERE status==0 是转移守卫——
    # expire 的 60s 扫描并发把 0→3 时，这里影响行数为 0 → 拒绝审核，
    # 不把已过期的预约覆盖成通过/拒绝。
    updated = (
        db.query(Reservation)
        .filter(Reservation.id == reservation_id, Reservation.status == 0)
        .update({"status": status}, synchronize_session=False)
    )
    if not updated:
        raise BusinessException(message="当前状态不支持审核")
    db.commit()


def expire_pending_reservations():
    """把过期的待审核单标成已取消（日期已过 / 当天已过结束时间）。

    2026-11-03 改：原来把 status==0 全量拉进 Python 逐条比字符串，
    现在单条条件 UPDATE 在 SQL 端过滤；WHERE 里的 status==0 同时是
    转移守卫——与 audit/cancel 并发时不会覆盖别人的转移结果。
    """
    db = SessionLocal()
    try:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        now_time = now.strftime("%H:%M")
        updated = (
            db.query(Reservation)
            .filter(
                Reservation.status == 0,
                or_(
                    Reservation.date < today,
                    and_(Reservation.date == today, Reservation.end_time < now_time),
                ),
            )
            .update({"status": 3}, synchronize_session=False)
        )
        if updated:
            db.commit()
    finally:
        db.close()


async def run_expire_scan():
    while True:
        try:
            expire_pending_reservations()
        except Exception:  # noqa: BLE001
            # 2026-11-03 补：不兜底的话一次 DB 抖动就让后台任务静默死亡，
            # 过期扫描从此停摆到重启。记日志，下一轮接着扫。
            logging.exception("[reservation] 过期扫描失败，60s 后重试")
        await asyncio.sleep(60)
