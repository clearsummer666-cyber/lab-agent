from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Lab(Base):
    __tablename__ = "labs"
    __table_args__ = (
        # 实验室名唯一。理由同 users.username：service 查重挡不住并发。
        UniqueConstraint("name", name="uk_lab_name"),
        {"comment": "实验室信息表"},
    )

    name: Mapped[str] = mapped_column(
        String(50), comment="实验室名称", nullable=False
    )
    location: Mapped[str | None] = mapped_column(String(100), comment="位置")
    capacity: Mapped[int] = mapped_column(Integer, comment="容纳人数", default=0)
    open_time: Mapped[str | None] = mapped_column(String(20), comment="开放时间")
    close_time: Mapped[str | None] = mapped_column(String(20), comment="关闭时间")
    description: Mapped[str | None] = mapped_column(String(500), comment="简介")
    img: Mapped[str | None] = mapped_column(String(200), comment="封面图")
    status: Mapped[int] = mapped_column(default=1, comment="状态：0-关闭，1-开放")
