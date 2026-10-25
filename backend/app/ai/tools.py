"""Agent 能用的工具。对齐原项目 `agent_tools.py` 的契约。

八个对外工具 + 一个内部动作：
    search_lab_docs          查规则/开放时间（RAG）
    list_open_labs           查哪些实验室开着
    list_lab_equipments      查某个实验室的设备
    list_availability        查某天某个实验室的空闲时段（lab-ai 新增）
    get_today                拿今天的日期
    create_lab_reservation   提交预约（含人工确认 + 幂等）
    list_my_reservations     查我的预约
    cancel_my_reservation    取消预约
    save_slot                内部动作：把抽到的字段存进 state

★ 三条契约（L2-PLAN 第五节）：
  1. 工具名保留原项目的，前端 `TOOL_LABELS` 按 name 匹配中文文案，改名=前端要改
  2. 一律返回 JSON 字符串，出错返回 {"ok": false, "error": ...}，**不抛异常**
     —— 抛异常会打断 Agent 循环，模型拿不到错误就没法自己纠
  3. `make_tools(store)` 是工厂：每个 Agent 实例一张干净的幂等表，评测用例之间不污染
"""

import datetime
import json
from typing import Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from app.ai.repo import SqlRepo

STATUS_TEXT = {0: "待审核", 1: "已通过", 2: "已拒绝", 3: "已取消"}

SLOT_KEYS = ("lab_name", "book_date", "start_time", "end_time", "people")


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def make_tools(store: Any = None, repo: SqlRepo | None = None,
               require_confirm: bool = False) -> list:
    """造一套工具。

    store            幂等表。传 None 表示不做 Agent 层缓存，直接落库
                     （接原项目时用：DB 的唯一索引 + 行锁已经是真防线）
    repo             数据源。不传就用内存假数据；接原项目时传 SqlRepo
    require_confirm  True  = 用 interrupt 硬闸门（lab-ai 演示/评测）
                     False = 不拦，靠 prompt 约束（原项目前端没有确认弹窗）
    """
    if repo is None:
        raise ValueError("必须传 repo=SqlRepo(db, user_id)")

    # ── 1. 知识库检索 ──────────────────────────────────
    @tool
    def search_lab_docs(query: str) -> str:
        """查询实验室的规则、开放时间、安全须知、设备借用等说明。

        Args:
            query: 要查的内容，如"开放时间""取消规则"
        """
        return repo.search_docs(query)

    # ── 2. 查开放实验室 ────────────────────────────────
    @tool
    def list_open_labs(keywords: str = "") -> str:
        """列出当前开放的实验室。可按名称或位置筛选。

        Args:
            keywords: 关键词，留空表示全部
        """
        rows = repo.find_labs(keyword=keywords, only_open=True)
        return json.dumps({
            "ok": True,
            "total": len(rows),
            "labs": [
                {"id": x["id"], "name": x["name"], "location": x["location"],
                 "capacity": x["capacity"], "open_time": x["open"]}
                for x in rows
            ],
        }, ensure_ascii=False)

    # ── 3. 查设备 ──────────────────────────────────────
    @tool
    def list_lab_equipments(lab_id: int, keywords: str = "") -> str:
        """查某个实验室有哪些设备。

        Args:
            lab_id: 实验室 ID（先用 list_open_labs 查到）
            keywords: 设备关键词，留空表示全部
        """
        lab = repo.get_lab(lab_id)
        if lab is None:
            return _err(f"没有 id={lab_id} 的实验室")
        rows = repo.find_equipments(lab_id)
        if keywords:
            rows = [x for x in rows if keywords in x["name"]]
        return json.dumps({
            "ok": True,
            "total": len(rows),
            "equipments": [
                {"id": x["id"], "name": x["name"], "lab_id": x["lab_id"],
                 "lab_name": lab["name"], "spec": x["spec"],
                 "quantity": x["qty"], "status": x["status"]}
                for x in rows
            ],
        }, ensure_ascii=False)

    # ── 4. 查空闲时段（lab-ai 新增能力）─────────────────
    @tool
    def list_availability(book_date: str, lab_name: str = "") -> str:
        """查某天实验室的空闲时段。用户还没定时间时，用它给用户看。

        Args:
            book_date: 日期，格式 YYYY-MM-DD
            lab_name: 实验室名称，留空表示查全部
        """
        if len(book_date) != 10:
            return _err(f"book_date 要 YYYY-MM-DD，你给的是 {book_date}")
        rows = repo.list_availability(book_date)
        if lab_name:
            rows = [r for r in rows if lab_name in r["lab"]["name"]]
            if not rows:
                return _err(f"没有叫「{lab_name}」的开放实验室")
        return json.dumps({
            "ok": True,
            "date": book_date,
            "labs": [
                {"lab_id": r["lab"]["id"], "lab_name": r["lab"]["name"],
                 "capacity": r["lab"]["capacity"],
                 "free": [f"{s}-{e}" for s, e in r["free"]]}
                for r in rows
            ],
        }, ensure_ascii=False)

    # ── 5. 今天日期 ────────────────────────────────────
    @tool
    def get_today() -> str:
        """拿今天的日期。用户说「今天/明天/后天」时先调它，不要猜。"""
        return str(datetime.date.today())

    # ── 6. 提交预约（人工确认 + 幂等）───────────────────
    @tool
    def create_lab_reservation(
        lab_id: int,
        date: str,
        start_time: str,
        end_time: str,
        people: int = 1,
        equipment_id: int | None = None,
        remark: str | None = None,
        runtime: ToolRuntime = None,
    ) -> str | Command:
        """提交预约申请。信息齐了才调，参数一次填全。

        Args:
            lab_id: 实验室 ID（先用 list_open_labs 查到）
            date: 日期，格式 YYYY-MM-DD
            start_time: 开始时间 HH:MM
            end_time: 结束时间 HH:MM
            people: 使用人数
            equipment_id: 设备 ID，不用设备就不填
            remark: 备注，可不填
        """
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            return _err(f"date 要 YYYY-MM-DD，你给的是 {date}")

        lab = repo.get_lab(lab_id)
        if lab is None:
            return _err(f"没有 id={lab_id} 的实验室")
        if people > lab["capacity"]:
            return _err(
                f"{lab['name']}最多容纳 {lab['capacity']} 人，你说的 {people} 人超出容量"
            )

        # 设备校验（2026-10-25 补）：以前只写库不校验，
        # 会出现「约了别的实验室的设备」「约了维修中的设备」这种脏数据。
        if equipment_id is not None:
            eq = repo.get_equipment(equipment_id)
            if eq is None:
                return _err(f"没有 id={equipment_id} 的设备")
            if eq["lab_id"] != lab_id:
                return _err(
                    f"设备「{eq['name']}」属于实验室 id={eq['lab_id']}，不属于{lab['name']}"
                )
            if eq["status"] != 1:
                return _err(f"设备「{eq['name']}」当前在维修中，约不了")
        if not repo.is_available(lab_id, date, start_time, end_time):
            return _err(f"{date} {start_time}-{end_time} 已被占用，约不上")

        # ★ 暂停点。恢复时整个工具会从头重跑（第 14 课实测）
        #   接原项目时 require_confirm=False：前端没有确认弹窗，
        #   搬 interrupt 会让 SSE 永久卡在「正在思考…」，改用 prompt 约束。
        if require_confirm:
            decision = interrupt({
                "问": f"确认提交：{lab['name']} {date} {start_time}-{end_time}，{people} 人？"
            })
            if not decision.get("approved"):
                return _err(f"用户没有确认（{decision.get('note', '未说明')}），未提交")

        # 写库必须在 interrupt 之后，并且走幂等层（第 15 课）
        def _do_create():
            return repo.create(
                lab_id=lab_id, date=date, start=start_time, end=end_time,
                people=people, equipment_id=equipment_id, remark=remark,
            )

        try:
            # 幂等交给 DB：唯一索引 uk_reservation_exact + service 的 SELECT FOR UPDATE
            new_id, hit = _do_create(), False
        except Exception as exc:                  # noqa: BLE001
            # 原项目 service 会抛 BusinessException（该时段已预约等）。
            # 工具不能把异常抛出去——那会打断 Agent 循环，模型拿不到原因就没法自己纠。
            return _err(f"写入失败：{getattr(exc, 'message', str(exc))}")

        if hit:
            return _err(f"这个时段已经提交过了，单号 {new_id}，没有重复下单")

        msg = f"预约成功，单号 {new_id}，状态待审核，管理员确认后方可使用"
        if runtime is None:
            return _ok(message=msg, reservation_id=new_id)

        # ★ 只清槽位，不清历史。
        #   踩过的坑：这里如果 RemoveMessage(REMOVE_ALL_MESSAGES) 清空历史，
        #   会把「发起 tool_call 的那条 AI 消息」也删掉，只剩一条孤立的
        #   ToolMessage —— 下一轮模型调用直接失败（tool_call 和 tool_message 必须配对）。
        #   记忆退场交给 TTL 或用户新建会话，不在工具执行中间做。
        return Command(
            update={
                "slots": {},
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
            }
        )

    # ── 7. 查我的预约 ──────────────────────────────────
    @tool
    def list_my_reservations(status: int | None = None) -> str:
        """查当前用户的预约记录。

        Args:
            status: 0待审核 1已通过 2已拒绝 3已取消；不填表示全部
        """
        rows = repo.list_mine(status)
        return json.dumps({
            "ok": True,
            "total": len(rows),
            "reservations": [
                {"reservation_id": x["id"],
                 "lab_name": x.get("lab_name") or (repo.get_lab(x["lab_id"]) or {}).get("name", "?"),
                 "date": x["date"], "start_time": x["start"], "end_time": x["end"],
                 "status": x["status"], "status_text": STATUS_TEXT[x["status"]]}
                for x in rows
            ],
        }, ensure_ascii=False)

    # ── 8. 取消预约 ────────────────────────────────────
    @tool
    def cancel_my_reservation(reservation_id: int) -> str:
        """取消一条预约。取消前必须先查出单号并向用户复述确认。

        Args:
            reservation_id: 预约单号（先用 list_my_reservations 查到）
        """
        ok, msg = repo.cancel(reservation_id)
        return _ok(message=msg) if ok else _err(msg)

    # ── 9. 内部动作：存槽位 ────────────────────────────
    @tool
    def save_slot(
        key: Literal["lab_name", "book_date", "start_time", "end_time", "people"],
        value: str,
        runtime: ToolRuntime,
    ) -> Command:
        """记录一个预约字段。抽到信息就立刻存，一次存一个。

        Args:
            key: 字段名，只能是这五个之一
            value: 值，原样存字符串
        """
        slots = dict(runtime.state.get("slots") or {})
        slots[key] = value
        return Command(
            update={
                "slots": slots,
                "messages": [
                    ToolMessage(f"已记录 {key}={value}", tool_call_id=runtime.tool_call_id)
                ],
            }
        )

    return [
        search_lab_docs,
        list_open_labs,
        list_lab_equipments,
        list_availability,
        get_today,
        create_lab_reservation,
        list_my_reservations,
        cancel_my_reservation,
        save_slot,
    ]
