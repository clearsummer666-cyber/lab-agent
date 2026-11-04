from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReservationPreview(Base):
    __tablename__ = "reservation_previews"

    preview_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    confirmation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lab_id: Mapped[int] = mapped_column(ForeignKey("labs.id"), nullable=False)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipments.id"), nullable=True)
    date: Mapped[str] = mapped_column(String(20), nullable=False)
    start_time: Mapped[str] = mapped_column(String(20), nullable=False)
    end_time: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="VALID", index=True, nullable=False)
