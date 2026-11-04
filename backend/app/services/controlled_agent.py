"""可持久化的显式状态机；同步数据库，异步原路由仅作薄适配。"""
import time
import uuid

from sqlalchemy.orm import Session

from app.models.agent_session import AgentSession
from app.models.reservation_preview import ReservationPreview
from app.models.user import User
from app.services import agent_run_service
from app.services.controlled_tools import ControlledTools

IDLE = "IDLE"
COLLECTING = "COLLECTING_REQUIREMENTS"
WAITING_SLOT = "WAITING_SLOT_SELECTION"
WAITING_CONFIRM = "WAITING_CONFIRMATION"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
RULE_QUESTION = "RULE_QUESTION"


def requirements(message: str, previous: dict) -> dict:
    import re
    result = dict(previous)
    found = re.search(r"20\d{2}-\d{2}-\d{2}", message)
    if found:
        result["date"] = found.group(0)
    if "PCR" in message.upper():
        result.update(lab_type="bio", equipment="PCR")
    elif "生物" in message:
        result["lab_type"] = "bio"
    elif "通风橱" in message or "FUME_HOOD" in message.upper():
        result.update(lab_type="chemistry", equipment="FUME")
    elif "化学" in message:
        result["lab_type"] = "chemistry"
    if "上午" in message:
        result.update(start_time="09:00", end_time="11:00")
    elif "下午" in message:
        result.update(start_time="14:00", end_time="16:00")
    return result


class ControlledAgent:
    def __init__(self, db: Session, user: User, payload):
        self.db, self.user, self.payload = db, user, payload
        self.session = db.query(AgentSession).filter(AgentSession.thread_id == payload.session_id).first()
        if self.session is None:
            self.session = AgentSession(thread_id=payload.session_id, user_id=user.id,
                                        state=IDLE, requirements_json={})
            db.add(self.session)
            db.commit()
        if self.session.user_id != user.id:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="会话不属于该用户")
        self.tool_calls, self.citations, self.data = [], [], {}
        self.tools = ControlledTools(db, user, payload.session_id, self._record)
        self.transitions = [self.session.state]

    def _state(self, state: str) -> None:
        self.session.state = state
        self.transitions.append(state)

    def _record(self, name: str, payload: dict) -> None:
        self.tool_calls.append({"name": name, "result": payload})
        self.citations.extend(payload.get("citations", []))

    def _search(self) -> list[dict]:
        req = self.session.requirements_json or {}
        if not req.get("date"):
            self._state(COLLECTING)
            return []
        result = self.tools.search_available_slots(req["date"], req.get("lab_type"),
            req.get("equipment"), req.get("start_time"), req.get("end_time"))
        candidates = result["candidate_slots"]
        self.data["candidate_slots"] = candidates
        self._state(WAITING_SLOT if candidates else FAILED)
        return candidates

    def _prepare(self, slot: dict) -> dict:
        result = self.tools.prepare_reservation(slot,
            enforce_qualifications=self.payload.mode == "candidate")
        if result.get("eligible"):
            self.session.preview_id = result["preview_id"]
            self.data.update(result)
            self._state(WAITING_CONFIRM)
        else:
            self._state(FAILED)
            if self.payload.mode == "candidate" and result.get("missing_rule_codes"):
                rules = self.tools.search_rules("PCR 未认证 严禁 预约 前置校验",
                    self.session.requirements_json.get("lab_type", "general"))
                self.data["rule_explanation"] = rules
        return result

    def _confirm(self) -> dict:
        preview_id = self.payload.preview_id or self.session.preview_id
        if not preview_id:
            self._state(FAILED)
            return {"success": False, "error": "请先取得有效的预约预览"}
        preview = self.db.query(ReservationPreview).filter(
            ReservationPreview.preview_id == preview_id).first()
        if not preview or preview.user_id != self.user.id:
            self._state(FAILED)
            return {"success": False, "error": "预约预览无效"}
        idem = (self.payload.idempotency_key or f"{self.payload.session_id}:{preview_id}")[:64]
        try:
            result = self.tools.confirm_reservation(preview_id,
                self.payload.confirmation_id or preview.confirmation_id, idem)
        except TimeoutError:
            if self.payload.mode == "candidate":
                result = self.tools.get_reservation(idempotency_key=idem)
                self.data["recovered_after_timeout"] = True
            else:
                result = {"success": False, "error": "工具响应超时"}
        self.data.update(result)
        self._state(SUCCEEDED if result.get("success") or result.get("found") else FAILED)
        return result

    def run(self) -> dict:
        started = time.perf_counter()
        message = self.payload.message
        previous_state = self.session.state
        self.session.requirements_json = requirements(message, self.session.requirements_json or {})
        if self.payload.force_confirm_attempt:
            result = self._confirm()
            reply = "没有有效预览，已阻止强制写入。" if not result.get("success") else "预约已确认。"
        elif self.session.state == WAITING_CONFIRM and any(k in message for k in ("多久", "培训", "规定", "规则")):
            self._state(RULE_QUESTION)
            lab_type = "general" if any(k in message for k in ("取消", "爽约", "违约")) else \
                self.session.requirements_json.get("lab_type", "general")
            rules = self.tools.search_rules(message, lab_type)
            self.data["rule_explanation"] = rules
            self._state(WAITING_CONFIRM)
            reply = rules.get("answer") or "当前未检索到相关规则，请补充问题。"
        elif self.session.state == WAITING_CONFIRM and "改" in message and "确认" not in message:
            if self.payload.mode == "candidate":
                preview = self.db.query(ReservationPreview).filter(
                    ReservationPreview.preview_id == self.session.preview_id,
                    ReservationPreview.status == "VALID").first()
                if preview:
                    preview.status = "INVALIDATED"
                    self.db.commit()
            candidates = self._search()
            if candidates:
                result = self._prepare(candidates[0])
                reply = "已按新条件生成预约预览，请确认。" if result.get("eligible") else "新条件不满足预约要求。"
            else:
                reply = "未找到新条件下的可用时段。"
        elif "确认" in message:
            result = self._confirm()
            reply = ("预约确认成功，已核对数据库状态。" if result.get("success") or result.get("found")
                     else f"预约未完成：{result.get('error', '请检查预约状态')}。")
        else:
            candidates = self._search()
            if candidates and any(k in message for k in ("第一个", "生成预览", "准备", "选第一")):
                selected = next((item for item in candidates if item["slot_id"] == self.payload.selected_slot_id), candidates[0])
                result = self._prepare(selected)
                if result.get("eligible"):
                    reply = "预约预览已生成，核对时段后请明确确认。"
                else:
                    reply = self.data.get("rule_explanation", {}).get("answer") or "资质校验未通过，未创建预约。"
            elif candidates:
                reply = "已找到以下可用时段，请选择后生成预约预览。"
            elif self.session.state == COLLECTING:
                reply = "请提供预约日期。"
            else:
                reply = "没有符合条件的可用时段。"
        self.db.commit()
        if self.session.preview_id:
            self.data.setdefault("preview_id", self.session.preview_id)
            preview = self.db.query(ReservationPreview).filter(
                ReservationPreview.preview_id == self.session.preview_id).first()
            if preview:
                self.data.setdefault("confirmation_id", preview.confirmation_id)
        trace_id = f"trace_{uuid.uuid4().hex}"
        detail = {"state_transitions": self.transitions, "rag_citations": list(dict.fromkeys(self.citations)),
                  "tool_trace": self.tool_calls, "previous_state": previous_state}
        agent_run_service.record_run(self.db, user_id=self.user.id, thread_id=self.payload.session_id,
            question=message, status=int(self.session.state == FAILED),
            duration_ms=int((time.perf_counter() - started) * 1000),
            tool_calls=[item["name"] for item in self.tool_calls], input_tokens=len(message),
            output_tokens=len(reply), trace_id=trace_id, session_status=self.session.state,
            trace_detail=detail)
        return {"ok": self.session.state != FAILED, "message": reply,
                "structured_data": {**self.data, "state": self.session.state,
                                    "citations": detail["rag_citations"]},
                "trace_id": trace_id}
