import asyncio
import re

from datetime import datetime

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
    if data.date < current_date:
        raise BusinessException(message="预约日期不能小于当前的日期")
    if end_time <= start_time:
        raise BusinessException(message="预约的结束时间必须晚于开始时间")
    if data.date == current_date and start_time < current_time:
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

    # 区间重叠判定：已有 [start, end) 与新的 [start, end) 相交
    # 写成 start < new_end AND end > new_start，覆盖"包含/被包含/左交/右交"四种情况
    query = db.query(Reservation).filter(
        Reservation.lab_id == data.lab_id,
        Reservation.date == data.date,
        Reservation.status.in_([0, 1]),  # 0表示待审核  1已通过
        Reservation.start_time < end_time,
        Reservation.end_time > start_time,
    )
    if data.equipment_id:
        query = query.filter(
            Reservation.equipment_id == data.equipment_id
        )  # 预约实验室设备
    else:
        query = query.filter(Reservation.equipment_id.is_(None))  # 只预约实验室
    res = query.first()
    if res:
        raise BusinessException(message="该时段已预约")

    # ── 4. 插入：唯一索引冲突说明有并发写入漏网，转成业务提示 ──
    resvervation_model = Reservation(
        user_id=current_user.id,
        lab_id=data.lab_id,
        equipment_id=data.equipment_id,
        date=data.date,
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
    item.status = 3
    db.commit()


def audit_reservation(db: Session, reservation_id: int, status: int):
    """管理员审核预约"""
    if status not in [1, 2]:
        raise BusinessException(message="审核状态错误")
    item = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not item:
        raise BusinessException(message="预约记录不存在")
    if item.status != 0:
        raise BusinessException(message="当前状态不支持审核")
    item.status = status
    db.commit()


def expire_pending_reservations():
    db = SessionLocal()
    try:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        now_time = now.strftime("%H:%M")
        items = db.query(Reservation).filter(Reservation.status == 0).all()
        changed = False
        for item in items:
            if item.date < today or (item.date == today and item.end_time < now_time):
                item.status = 3
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()


async def run_expire_scan():
    while True:
        expire_pending_reservations()
        await asyncio.sleep(60)
