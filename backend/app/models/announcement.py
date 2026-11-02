from sqlalchemy import Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = {"comment": "实验室公告表"}

    title: Mapped[str] = mapped_column(String(100), comment="公告标题", nullable=False)
    content: Mapped[str] = mapped_column(Text, comment="公告正文", nullable=False)
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), comment="发布人", nullable=False
    )
    is_top: Mapped[int] = mapped_column(
        Integer, comment="是否置顶：0-否 1-是", nullable=False, default=0
    )
    status: Mapped[int] = mapped_column(
        Integer, default=1, comment="状态：0-下架 1-发布"
    )
