from sqlalchemy import JSON, Boolean, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RuleChunk(Base):
    """条款级知识单元；继承 Base 的业务主键与时间戳，不重复声明。"""

    __tablename__ = "rule_chunks"
    __table_args__ = (
        Index("idx_rule_chunks_scope", "lab_type", "valid_from", "valid_to"),
    )

    chunk_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    document_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    title_path: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lab_type: Mapped[str] = mapped_column(String(50), default="general", index=True)
    applicable_roles: Mapped[list] = mapped_column(JSON, default=list)
    rule_codes: Mapped[list] = mapped_column(JSON, default=list)
    valid_from: Mapped[str] = mapped_column(String(10), nullable=False)
    valid_to: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    parent_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(default=1)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    is_fixed_chunk: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
