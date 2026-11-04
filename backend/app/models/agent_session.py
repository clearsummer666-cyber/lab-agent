from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    thread_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(50), default="IDLE", nullable=False)
    requirements_json: Mapped[dict] = mapped_column(JSON, default=dict)
    preview_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
