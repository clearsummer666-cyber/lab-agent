"""合并后验收：原项目 Model、独立评测库、真实 HTTP 路径。"""
import json

from eval_suite.bootstrap import configure_eval_environment

configure_eval_environment()

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.agent_run import AgentRun
from app.models.reservation import Reservation
from app.models.reservation_preview import ReservationPreview

PRIMARY = "user_grad_qualified"
UNQUALIFIED = "user_grad_no_pcr_cert"


def _reset(client: TestClient) -> dict:
    response = client.post("/api/test/environment/reset")
    assert response.status_code == 200
    return response.json()["data"]


def _headers(client: TestClient, user_key: str = PRIMARY, **extra) -> dict:
    return {"X-Test-User-Id": str(client.eval_users[user_key]), **extra}


def chat(client: TestClient, session_id: str, message: str, user_key: str = PRIMARY, **extra):
    payload = {"session_id": session_id, "user_id": client.eval_users[user_key], "message": message, **extra}
    response = client.post("/api/agent/chat", json=payload, headers=_headers(client, user_key,
                            **({"X-Fault-Inject": payload.pop("fault_inject")} if "fault_inject" in payload else {})))
    assert response.status_code == 200, response.text
    return response.json()


def preview(client: TestClient, session_id: str = "test", message: str | None = None) -> dict:
    body = chat(client, session_id, message or "预约2026-11-06下午的PCR生物实验室，选第一个生成预览")
    assert body["structured_data"].get("preview_id"), body
    return body["structured_data"]


def confirm(client: TestClient, session_id: str, data: dict, token: str | None = None, **extra):
    return chat(client, session_id, "确认预约", preview_id=data["preview_id"],
                confirmation_id=token or data["confirmation_id"],
                idempotency_key=f"{session_id}:{data['preview_id']}"[:64], **extra)


def test_normal_flow_has_no_redundant_rule_search(client):
    data = preview(client)
    with SessionLocal() as db:
        trace = db.scalar(select(AgentRun).order_by(AgentRun.id))
        names = [call["name"] for call in json.loads(trace.trace_detail)["tool_trace"]]
        assert names == ["search_available_slots", "prepare_reservation"]
    assert confirm(client, "test", data)["structured_data"]["state"] == "SUCCEEDED"


def test_modification_invalidates_old_preview(client):
    old = preview(client)
    new = chat(client, "test", "改成2026-11-07上午的PCR实验室，重新生成预览")["structured_data"]
    with SessionLocal() as db:
        assert db.scalar(select(ReservationPreview).where(
            ReservationPreview.preview_id == old["preview_id"])).status == "INVALIDATED"
    rejected = confirm(client, "test", old)
    assert "无效" in rejected["message"]
    assert confirm(client, "test", new)["structured_data"]["state"] == "SUCCEEDED"


def test_rule_interrupt_restores_preview_context(client):
    before = preview(client)
    answer = chat(client, "test", "确认前问下最晚提前多久取消不会违约？")
    assert answer["structured_data"]["state"] == "WAITING_CONFIRMATION"
    assert answer["structured_data"]["preview_id"] == before["preview_id"]
    assert "2小时" in answer["message"]
    assert confirm(client, "test", answer["structured_data"])["structured_data"]["state"] == "SUCCEEDED"


def test_wrong_confirmation_token_is_rejected(client):
    data = preview(client)
    result = confirm(client, "test", data, token="wrong-token")
    assert result["structured_data"]["state"] == "FAILED"


def test_unqualified_and_empty_rule_search_never_write(client):
    data = chat(client, "no-cert", "预约2026-11-06下午的PCR生物实验室，选第一个生成预览", UNQUALIFIED)
    assert data["structured_data"]["state"] == "FAILED"
    payload = {"session_id": "empty-rag", "user_id": client.eval_users[UNQUALIFIED],
               "message": "预约2026-11-06下午的PCR生物实验室，选第一个生成预览"}
    response = client.post("/api/agent/chat", json=payload,
                           headers=_headers(client, UNQUALIFIED, **{"X-Fault-Inject": "RULE_SEARCH_EMPTY"}))
    assert response.status_code == 200
    assert "资质" in response.json()["message"] or "规则" in response.json()["message"]


def test_slot_takeover_and_idempotent_replay(client):
    data = preview(client)
    conflict = confirm(client, "test", data, **{"fault_inject": "SLOT_TAKEN_BEFORE_CONFIRM"})
    assert "占用" in conflict["message"]
    _reset(client)
    data = preview(client, "idempotent")
    first = confirm(client, "idempotent", data)
    second = confirm(client, "idempotent", data)
    assert second["structured_data"]["idempotent_replay"] is True
    assert first["structured_data"]["reservation_id"] == second["structured_data"]["reservation_id"]
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Reservation)))) == 1


def test_timeout_recovers_existing_reservation(client):
    data = preview(client)
    result = confirm(client, "test", data, **{"fault_inject": "TOOL_TIMEOUT_AFTER_WRITE"})
    assert result["structured_data"]["recovered_after_timeout"] is True
    assert result["structured_data"]["state"] == "SUCCEEDED"


def test_general_and_special_rules_and_static_codes(client):
    response = client.post("/api/rag/query", json={
        "question": "研究生预约化学实验室的危化品特种通风橱，需要提前几天申请？单次最多能约几个小时？",
        "user_role": "graduate_student", "lab_type": "chemistry", "strategy": "D"})
    data = response.json()
    assert "general_rules_2026_01_art_01" in data["citations"]
    assert "chem_rules_2026_01_art_04" in data["citations"]
    assert "MAX_DURATION_4_HOURS" in data["rule_codes"]
    assert "ADVANCE_3_DAYS_HAZCHEM" in data["rule_codes"]
    assert data["parent_contexts"]


def test_expired_and_wrong_role_are_filtered(client):
    response = client.post("/api/rag/query", json={
        "question": "我是研究生，已经通过了实验室安全培训，请问周末可以预约化学实验室吗？",
        "user_role": "graduate_student", "lab_type": "chemistry", "strategy": "D"})
    citations = response.json()["citations"]
    assert "chem_rules_2025_expired_art_03" not in citations
    assert "chem_rules_2026_01_art_01" not in citations


def test_cross_session_user_binding(client):
    preview(client, "bound", "预约2026-11-06下午的PCR生物实验室，选第一个生成预览")
    response = client.post("/api/agent/chat", json={
        "session_id": "bound", "user_id": client.eval_users[UNQUALIFIED], "message": "确认"},
        headers=_headers(client, UNQUALIFIED))
    assert response.status_code == 403


def test_legacy_ai_contract_and_idle_session(client):
    quota = client.get("/api/ai/quota", headers=_headers(client))
    assert quota.status_code == 200
    assert quota.json()["code"] == 200
    assert "run_left" in quota.json()["data"]
    reset = client.post("/api/ai/session/reset", headers=_headers(client))
    assert reset.status_code == 200
    thread_id = reset.json()["data"]["thread_id"]
    assert thread_id.startswith(f"user:{client.eval_users[PRIMARY]}:s")
    from app.models.agent_session import AgentSession
    with SessionLocal() as db:
        session = db.scalar(select(AgentSession).where(AgentSession.thread_id == thread_id))
        assert session is not None and session.state == "IDLE"


def test_trace_and_reservation_read_endpoints(client):
    data = preview(client)
    confirmed = confirm(client, "test", data)
    trace = client.get(f"/api/traces/{confirmed['trace_id']}")
    assert trace.status_code == 200
    body = trace.json()
    assert body["session_status"] == "SUCCEEDED"
    assert "confirm_reservation" in body["tool_calls"]
    assert body["trace_detail"]["state_transitions"][-1] == "SUCCEEDED"
    reservation = client.get(f"/api/reservations/{confirmed['structured_data']['reservation_id']}")
    assert reservation.status_code == 200
    assert reservation.json()["status"] == 1
    assert reservation.json()["idempotency_key"]


import pytest


@pytest.fixture()
def client():
    with TestClient(app) as http:
        fixture = _reset(http)
        http.eval_users = fixture["user_ids"]
        yield http
