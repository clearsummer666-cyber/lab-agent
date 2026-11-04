from typing import Any, Literal
from pydantic import BaseModel, Field


class ControlledChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    user_id: int | str | None = None
    message: str = Field(min_length=1)
    selected_slot_id: str | None = None
    selected_lab_id: int | None = None
    preview_id: str | None = None
    confirmation_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)
    force_confirm_attempt: bool = False
    mode: Literal["baseline", "candidate"] = "candidate"


class RagQueryRequest(BaseModel):
    question: str
    user_role: str = "graduate_student"
    lab_type: str = "general"
    effective_date: str = "2026-11-05"
    strategy: Literal["A", "B", "C", "D"] = "D"
