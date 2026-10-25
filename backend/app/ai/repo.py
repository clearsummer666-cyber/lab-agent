"""数据访问层：接原项目的 MySQL。

这个文件是从 `lab-ai/agent/repo.py` 精简来的——只保留 SqlRepo。
MemoryRepo 留在 lab-ai 里给评测和教学用。

★ 依赖原项目的 `app` 包，必须在 lab-agent/backend 下运行。
"""

from contextlib import contextmanager
from typing import Any


class SqlRepo:
    """接原项目的 MySQL。真交付用这个。

    用法（在原项目的 FastAPI 依赖里）：
        repo = SqlRepo(db, user_id=current_user.id)
        agent = build_agent(db, current_user, repo=repo)

    ★ 依赖原项目的 `app` 包，必须在 lab-agent/backend 下运行，或用原项目的 venv。
    """

    def __init__(self, db: Any, user_id: int):
        # db 只为兼容调用方签名保留，**内部查询不再用它**（见 _session 的说明）
        self.db = db
        self.user_id = user_id

    @contextmanager
    def _session(self):
        """每次数据库操作拿一个**独立** Session，用完就关。

        ★ 为什么不能复用外面传进来的 db（2026-10-25 踩到的真 bug）

          FastAPI 的 `get_db` 是在请求的线程里建的 Session，
          但 LangGraph 会把**同步工具**扔进线程池执行（asyncio.to_thread），
          于是同一个 pymysql 连接被两个线程同时用，报：

              pymysql.err.InternalError: Packet sequence number wrong - got 1 expected 1

          SQLAlchemy Session 本来就不是线程安全的。12 条端到端评测里偶发 2 条，
          多用户并发时会更容易触发。

          **这个坑是「把 run_agent 改成 async」引入的**：
          同步 invoke() 时全链路一个线程，安全；改成 astream_events() 后工具跑在线程池。

        ★ 为什么不是加锁 serialize

          加锁能修正确性，但会把所有用户的数据库操作串成一条线。
          每次操作自己开一个连接，PostgreSQL/MySQL 的连接池本来就为此设计，
          并发度更好，代码也不用到处 with lock。
        """
        from app.database import SessionLocal

        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    @property
    def user_key(self) -> str:
        # 闭包捕获 current_user.id，不经过 state（state 要序列化，塞 ORM 会炸）
        return f"user:{self.user_id}"

    # ── 实验室 ────────────────────────────────────────
    def find_labs(self, keyword: str = "", only_open: bool = True) -> list[dict]:
        with self._session() as s:
            return self._find_labs(s, keyword, only_open)

    def _find_labs(self, s, keyword: str, only_open: bool) -> list[dict]:
        """给定 session 查实验室。抽出来是为了让 list_availability 复用同一个连接。"""
        from app.models.lab import Lab

        q = s.query(Lab)
        if only_open:
            q = q.filter(Lab.status == 1)
        if keyword:
            q = q.filter(Lab.name.like(f"%{keyword}%"))
        return [self._lab_dict(x) for x in q.all()]

    def get_lab(self, lab_id: int) -> dict | None:
        from app.models.lab import Lab

        with self._session() as s:
            row = s.get(Lab, lab_id)
            return self._lab_dict(row) if row else None

    @staticmethod
    def _lab_dict(x) -> dict:
        return {
            "id": x.id,
            "name": x.name,
            "location": x.location or "",
            "capacity": x.capacity or 0,
            "open": f"{x.open_time or '08:00'}-{x.close_time or '21:00'}",
            "status": x.status,
        }

    # ── 设备 ──────────────────────────────────────────
    def find_equipments(self, lab_id: int, keywords: str = "") -> list[dict]:
        from app.models.equipment import Equipment

        with self._session() as s:
            q = s.query(Equipment).filter(Equipment.lab_id == lab_id)
            if keywords:
                q = q.filter(Equipment.name.like(f"%{keywords}%"))
            return [
                {"id": x.id, "lab_id": x.lab_id, "name": x.name, "spec": x.spec or "",
                 "qty": x.quantity, "status": x.status}
                for x in q.all()
            ]

    def get_equipment(self, equipment_id: int) -> dict | None:
        from app.models.equipment import Equipment

        with self._session() as s:
            row = s.get(Equipment, equipment_id)
            if row is None:
                return None
            return {"id": row.id, "lab_id": row.lab_id, "name": row.name,
                    "spec": row.spec or "", "qty": row.quantity, "status": row.status}

    # ── 时段冲突 ──────────────────────────────────────
    def is_available(self, lab_id: int, date: str, start: str, end: str) -> bool:
        from app.models.reservation import Reservation

        with self._session() as s:
            # 区间重叠：[start, end) 与已有区间相交。已取消(3)不算占用
            n = (
                s.query(Reservation)
                .filter(
                    Reservation.lab_id == lab_id,
                    Reservation.date == date,
                    Reservation.status != 3,
                    Reservation.start_time < end,
                    Reservation.end_time > start,
                )
                .count()
            )
            return n == 0

    def list_availability(self, date: str, lab_name: str = "") -> list[dict]:
        from app.models.reservation import Reservation

        out = []
        with self._session() as s:
            for lab in self._find_labs(s, lab_name, only_open=True):
                busy = [
                    (x.start_time, x.end_time)
                    for x in s.query(Reservation).filter(
                        Reservation.lab_id == lab["id"],
                        Reservation.date == date,
                        Reservation.status != 3,
                    ).all()
                ]
                open_t, close_t = lab["open"].split("-")
                out.append({
                    "lab": lab,
                    "free": _free_slots(open_t, close_t, sorted(busy)),
                })
        return out

    # ── 落库：走原项目 service，复用它的全部校验 ────────
    def create(self, *, lab_id: int, date: str, start: str, end: str,
               people: int = 1, equipment_id: int | None = None,
               remark: str | None = None) -> int:
        from app.models.reservation import Reservation
        from app.models.user import User
        from app.schemas.reservation import ReservationCreateRequest
        from app.services import reservation_service

        with self._session() as s:
            user = s.get(User, self.user_id)
            if user is None:
                raise ValueError(f"没有 id={self.user_id} 的用户")

            req = ReservationCreateRequest(
                lab_id=lab_id, equipment_id=equipment_id, date=date,
                start_time=start, end_time=end, people=people, remark=remark,
            )
            # ★ 原项目的 create_reservation 没有 return（实测返回 None），
            #   所以调完要按唯一键把刚写的那条查回来。
            reservation_service.create_reservation(s, user, req)

            row = (
                s.query(Reservation)
                .filter(
                    Reservation.user_id == user.id,
                    Reservation.lab_id == lab_id,
                    Reservation.date == date,
                    Reservation.start_time == start,
                    Reservation.end_time == end,
                )
                .order_by(Reservation.id.desc())
                .first()
            )
            if row is None:
                raise RuntimeError("写入后没查回预约记录")
            return row.id

    # ── 我的预约 ──────────────────────────────────────
    def list_mine(self, status: int | None = None) -> list[dict]:
        from app.models.lab import Lab
        from app.models.reservation import Reservation

        with self._session() as s:
            q = s.query(Reservation).filter(Reservation.user_id == self.user_id)
            if status is not None:
                q = q.filter(Reservation.status == status)
            out = []
            for x in q.all():
                lab = s.get(Lab, x.lab_id)
                out.append({
                    "id": x.id,
                    "lab_id": x.lab_id,
                    "lab_name": lab.name if lab else "?",
                    "date": x.date,
                    "start": x.start_time,
                    "end": x.end_time,
                    "status": x.status,
                })
            return out

    def cancel(self, reservation_id: int) -> tuple[bool, str]:
        from app.models.user import User
        from app.services import reservation_service

        with self._session() as s:
            user = s.get(User, self.user_id)
            if user is None:
                return False, f"没有 id={self.user_id} 的用户"
            try:
                reservation_service.cancel_reservation(s, user, reservation_id)
                return True, f"单号 {reservation_id} 已取消"
            except Exception as exc:                      # noqa: BLE001
                return False, getattr(exc, "message", str(exc))

    # ── 知识库 ────────────────────────────────────────
    def search_docs(self, query: str) -> str:
        # kb_service.search 直接返回拼好的字符串（不是 list）。
        # 按 list 处理会变成逐字符 join —— 必须原样返回。
        # 不走 MySQL，所以不需要 session。
        from app.services import kb_service

        return kb_service.search(query) or "知识库里没有找到相关内容。"


def _free_slots(open_t: str, close_t: str, busy: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """从开放区间里挖掉被占的段，返回空档。"""
    free, cursor = [], open_t
    for s, e in busy:
        if s > cursor:
            free.append((cursor, min(s, close_t)))
        cursor = max(cursor, e)
    if cursor < close_t:
        free.append((cursor, close_t))
    return [(a, b) for a, b in free if a < b]
