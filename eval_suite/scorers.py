"""三层评分：真实业务记录、工具轨迹、语义条件。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.lab import Lab
from app.models.reservation import Reservation
from app.models.reservation_preview import ReservationPreview


def reciprocal_rank(retrieved: list[str], expected: list[str]) -> float:
    for rank, chunk_id in enumerate(retrieved, 1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


def keyword_coverage(answer: str, keywords: list[str]) -> float:
    return sum(keyword in answer for keyword in keywords) / len(keywords) if keywords else 1.0


class BusinessStateScorer:
    def score(self, db: Session, case: dict, user_id: int, final_session_state: str) -> dict:
        expected = case["expected_business_state"]
        reservations = list(db.scalars(select(Reservation).where(Reservation.user_id == user_id)))
        active = [item for item in reservations if item.status == 1]
        expected_count = expected.get("reservation_count_for_user")
        checks = {"reservation_count": expected_count is None or len(active) == expected_count,
                  "no_duplicate_idempotency_keys": len({r.idempotency_key for r in reservations}) == len(reservations),
                  "final_session_state": final_session_state == expected["final_session_state"]}
        if "reserved_date" in expected:
            checks["reserved_date"] = any(item.date == expected["reserved_date"] for item in active)
        if "invalidated_preview_count" in expected:
            count = db.scalar(select(func.count()).select_from(ReservationPreview).where(
                ReservationPreview.user_id == user_id, ReservationPreview.status == "INVALIDATED"))
            checks["invalidated_preview"] = count == expected["invalidated_preview_count"]
        return {"passed": all(checks.values()), "checks": checks,
                "active_reservations": len(active), "reservation_rows": len(reservations)}


class TrajectoryScorer:
    def score(self, tool_calls: list[dict], expected: dict, citations: list[str]) -> dict:
        names = [call["name"] for call in tool_calls]
        required, cursor = expected.get("must_call_tools_in_order", []), 0
        for name in names:
            if cursor < len(required) and name == required[cursor]:
                cursor += 1
        checks = {"ordered_tools": cursor == len(required),
                  "forbidden_tools_absent": not any(name in expected.get("forbidden_tools", []) for name in names),
                  "timeout_reconciliation": (not any(c.get("timeout_after_write") for c in tool_calls)
                                             or "get_reservation" in names)}
        if expected.get("must_cite_chunk"):
            checks["required_citation"] = expected["must_cite_chunk"] in citations
        return {"passed": all(checks.values()), "checks": checks, "tool_names": names}


class SemanticConditionScorer:
    def score(self, answer: str, expected: dict, citations: list[str]) -> dict:
        keyword = expected.get("must_explain_keyword")
        checks = {"keyword": keyword is None or keyword in answer,
                  "required_citation": not expected.get("must_cite_chunk") or expected["must_cite_chunk"] in citations}
        return {"passed": all(checks.values()), "checks": checks}
