from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.equipment import Equipment
from app.models.lab import Lab
from app.models.user import User


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        # 兜底约束：防住"完全相同时间段"的重复写入（比如绕过 service 直接写库）。
        # 区间重叠（09:00-11:00 与 10:00-12:00）唯一索引防不住，
        # 真正的并发防线是 service 层对实验室行的 SELECT ... FOR UPDATE。
        # 注意：equipment_id 可为 NULL，而 MySQL 唯一索引里 NULL 互不相等，
        # 所以"只约实验室"的记录这条约束不生效，仍需依赖行锁。
        UniqueConstraint(
            "lab_id",
            "equipment_id",
            "date",
            "start_time",
            "end_time",
            name="uk_reservation_exact",
        ),
        {"comment": "预约信息"},
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), comment="预约人", nullable=False
    )
    lab_id: Mapped[int] = mapped_column(
        ForeignKey("labs.id"), comment="实验室", nullable=False
    )
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipments.id"), comment="设备，空表示预约实验室", nullable=True
    )
    date: Mapped[str] = mapped_column(String(20), comment="预约日期", nullable=False)
    start_time: Mapped[str] = mapped_column(
        String(20), comment="开始时间", nullable=False
    )
    end_time: Mapped[str] = mapped_column(
        String(20), comment="结束时间", nullable=False
    )
    remark: Mapped[str] = mapped_column(String(255), comment="备注", nullable=True)
    status: Mapped[int] = mapped_column(
        comment="预约状态 0待审核，1已通过，2已拒绝，3已取消", default=0
    )

    user: Mapped[User] = relationship()
    lab: Mapped[Lab] = relationship()
    equipment: Mapped[Equipment] = relationship()
