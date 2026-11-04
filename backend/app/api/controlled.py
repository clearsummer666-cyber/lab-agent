import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import engine, get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.controlled import ControlledChatRequest, RagQueryRequest
from app.services import agent_service, kb_service

router = APIRouter(tags=["受控RAG与Agent"])


@router.post("/rag/query")
def rag_query(payload: RagQueryRequest):
    return kb_service.query_rules(
        payload.question, strategy=payload.strategy, lab_type=payload.lab_type,
        user_role=payload.user_role, effective_date=payload.effective_date,
    )


@router.post("/agent/chat")
def controlled_chat(payload: ControlledChatRequest,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    if payload.mode == "baseline" and not os.environ.get("EVAL_MODE"):
        raise HTTPException(status_code=400, detail="baseline 仅用于评测模式")
    if payload.user_id is not None and str(payload.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="会话不属于该用户")
    return agent_service.run_controlled(db, user, payload, mode=payload.mode)


@router.get("/traces/{trace_id}")
def trace(trace_id: str, db: Session = Depends(get_db)):
    item = agent_service.get_trace(db, trace_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    try:
        detail = json.loads(item.trace_detail) if item.trace_detail else {}
    except json.JSONDecodeError:
        detail = {"raw": item.trace_detail}
    return {"trace_id": item.trace_id, "thread_id": item.thread_id,
            "tool_calls": item.tool_calls.split(",") if item.tool_calls else [],
            "session_status": item.session_status, "latency_ms": item.duration_ms,
            "trace_detail": detail, **detail}


@router.get("/reservations/{reservation_id}")
def reservation(reservation_id: int, db: Session = Depends(get_db)):
    item = agent_service.get_controlled_reservation(db, reservation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {"reservation_id": item.id, "id": item.id, "user_id": item.user_id,
            "lab_id": item.lab_id, "equipment_id": item.equipment_id,
            "date": item.date, "start_time": item.start_time,
            "end_time": item.end_time, "status": item.status,
            "idempotency_key": item.idempotency_key}


def _is_eval_database() -> bool:
    """只允许重置仓库内的可再生评测库，避免误伤业务库。"""
    database = getattr(getattr(engine, "url", None), "database", None)
    if not database:
        return False
    expected = Path(__file__).resolve().parents[3] / "data" / "lab_eval.db"
    try:
        return Path(database).resolve() == expected.resolve()
    except (OSError, ValueError):
        return False


@router.post("/test/environment/reset")
def reset(db: Session = Depends(get_db)):
    if not os.environ.get("EVAL_MODE") or not _is_eval_database():
        raise HTTPException(status_code=404, detail="Not Found")
    from eval_suite.fixtures import reset_test_environment
    return Response.success(data=reset_test_environment(db))
