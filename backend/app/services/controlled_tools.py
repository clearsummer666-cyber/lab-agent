"""原项目 Model 上的五个受控工具，状态、身份与确认门禁均由服务端维护。"""
import re
import uuid
from contextvars import ContextVar
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.equipment import Equipment
from app.models.lab import Lab
from app.models.reservation import Reservation
from app.models.reservation_preview import ReservationPreview
from app.models.user import User
from app.schemas.reservation import ReservationCreateRequest
from app.services import kb_service, reservation_service

fault_inject: ContextVar[str | None] = ContextVar("fault_inject", default=None)
PCR_NAMES = ("PCR", "聚合酶", "生物安全柜")
FUME_NAMES = ("通风橱", "FUME", "危化")


def qualification_set(user: User) -> set[str]:
    return {item.strip() for item in (user.qualifications or "").split(",") if item.strip()}


def equipment_required_codes(equipment: Equipment | None) -> set[str]:
    if equipment is None:
        return set()
    text = f"{equipment.name} {equipment.spec or ''}".upper()
    if any(item.upper() in text for item in PCR_NAMES):
        return {"SAFETY_TRAINING_REQUIRED", "PCR_TRAINING_REQUIRED"}
    if any(item.upper() in text for item in FUME_NAMES):
        return {"SAFETY_TRAINING_REQUIRED", "HAZCHEM_CERT_REQUIRED"}
    return {"SAFETY_TRAINING_REQUIRED"}


def slot_dict(slot: dict) -> dict:
    return dict(slot)


class ControlledTools:
    def __init__(self, db: Session, user: User, thread_id: str, record=None):
        self.db, self.user, self.thread_id = db, user, thread_id
        self.record = record or (lambda _name, _payload: None)

    def _record(self, name: str, result: dict, **extra) -> dict:
        self.record(name, {**result, **extra})
        return result

    def search_rules(self, query: str, lab_type: str = "general") -> dict:
        if fault_inject.get() == "RULE_SEARCH_EMPTY":
            return self._record("search_rules", {"citations": [], "rule_codes": [],
                                                  "answer": "当前未检索到规则，请补充资质后重试。"})
        role = {"student": "graduate_student", "admin": "faculty"}.get(self.user.role, self.user.role)
        result = kb_service.query_rules(query, lab_type=lab_type, user_role=role)
        return self._record("search_rules", result)

    def search_available_slots(self, date: str, lab_type: str | None = None,
                               equipment: str | None = None,
                               preferred_start: str | None = None,
                               preferred_end: str | None = None) -> dict:
        query = self.db.query(Lab).filter(Lab.status == 1)
        if lab_type and lab_type != "general":
            query = query.filter(Lab.lab_type.in_([lab_type, "bio" if lab_type == "biology" else lab_type]))
        labs = query.order_by(Lab.id).all()
        output = []
        start = preferred_start or ("14:00" if equipment == "pm" else "08:00")
        end = preferred_end or ("16:00" if start < "12:00" else "18:00")
        for lab in labs:
            equipments = self.db.query(Equipment).filter(Equipment.lab_id == lab.id,
                                                           Equipment.status == 1).all()
            if equipment and equipment not in ("am", "pm"):
                equipments = [item for item in equipments if equipment.upper() in
                              f"{item.name} {item.spec or ''}".upper()]
                if not equipments:
                    continue
            candidates = equipments or [None]
            for item in candidates:
                # 全量重叠校验，而不是只比完全相同时间。
                overlap = self.db.query(Reservation).filter(
                    Reservation.lab_id == lab.id, Reservation.date == date,
                    Reservation.status.in_([0, 1]),
                    Reservation.start_time < end, Reservation.end_time > start,
                )
                if item:
                    overlap = overlap.filter(or_(Reservation.equipment_id.is_(None),
                                                 Reservation.equipment_id == item.id))
                if overlap.first():
                    continue
                output.append({"slot_id": f"lab:{lab.id}:equipment:{item.id if item else 0}:{date}:{start}-{end}",
                               "lab_id": lab.id, "equipment_id": item.id if item else None,
                               "lab_name": lab.name, "lab_type": lab.lab_type,
                               "date": date, "start_time": start, "end_time": end,
                               "time_range": f"{start}-{end}", "status": "AVAILABLE",
                               "required_rule_codes": sorted(equipment_required_codes(item))})
        return self._record("search_available_slots", {"candidate_slots": output})

    @staticmethod
    def parse_slot(slot_id: str) -> tuple[int, int | None]:
        match = re.fullmatch(r"lab:(\d+):equipment:(\d+):[^:]+:[^:]+", slot_id)
        if not match:
            raise BusinessException(message="时段标识无效")
        return int(match.group(1)), int(match.group(2)) or None

    def prepare_reservation(self, slot: dict, enforce_qualifications: bool = True) -> dict:
        lab = self.db.get(Lab, slot["lab_id"])
        equipment = self.db.get(Equipment, slot["equipment_id"]) if slot.get("equipment_id") else None
        if not lab or lab.status != 1:
            return self._record("prepare_reservation", {"eligible": False, "error": "实验室不可用"})
        missing = sorted(equipment_required_codes(equipment) - qualification_set(self.user))
        if enforce_qualifications and missing:
            return self._record("prepare_reservation", {"eligible": False, "missing_rule_codes": missing})
        preview = ReservationPreview(preview_id=f"prev_{uuid.uuid4().hex}",
            confirmation_id=f"confirm_{uuid.uuid4().hex}", user_id=self.user.id,
            lab_id=lab.id, equipment_id=equipment.id if equipment else None,
            date=slot["date"], start_time=slot["start_time"], end_time=slot["end_time"])
        self.db.add(preview)
        self.db.commit()
        return self._record("prepare_reservation", {
            "eligible": True, "preview_id": preview.preview_id,
            "confirmation_id": preview.confirmation_id, "slot": slot_dict(slot),
            "idempotency_key": f"{self.thread_id}:{preview.preview_id}"[:64]})

    def confirm_reservation(self, preview_id: str, confirmation_id: str,
                            idempotency_key: str) -> dict:
        existing = self.db.query(Reservation).filter(
            Reservation.idempotency_key == idempotency_key,
            Reservation.user_id == self.user.id).first()
        if existing:
            return self._record("confirm_reservation", {"success": True,
                "reservation_id": existing.id, "id": existing.id, "status": existing.status,
                "idempotent_replay": True})
        preview = self.db.query(ReservationPreview).filter(ReservationPreview.preview_id == preview_id).first()
        if not preview or preview.user_id != self.user.id or preview.status != "VALID" \
                or preview.confirmation_id != confirmation_id:
            return self._record("confirm_reservation", {"success": False,
                                                       "error": "预约预览无效或确认令牌不匹配"})
        if fault_inject.get() == "SLOT_TAKEN_BEFORE_CONFIRM":
            competitor = self.db.query(User).filter(User.username == "eval_competitor").first()
            occupied = Reservation(user_id=competitor.id if competitor else self.user.id,
                lab_id=preview.lab_id, equipment_id=preview.equipment_id, date=preview.date,
                start_time=preview.start_time, end_time=preview.end_time,
                idempotency_key=f"competitor-{uuid.uuid4().hex}"[:64], status=1)
            self.db.add(occupied)
            self.db.commit()
        data = ReservationCreateRequest(lab_id=preview.lab_id, equipment_id=preview.equipment_id,
            date=preview.date, start_time=preview.start_time, end_time=preview.end_time)
        try:
            reservation_service.create_reservation(self.db, self.user, data)
            reservation = self.db.query(Reservation).filter(Reservation.user_id == self.user.id,
                Reservation.lab_id == preview.lab_id, Reservation.date == preview.date,
                Reservation.start_time == preview.start_time,
                Reservation.end_time == preview.end_time).order_by(Reservation.id.desc()).first()
            reservation.idempotency_key = idempotency_key
            reservation.status = 1
            preview.status = "CONSUMED"
            self.db.commit()
        except BusinessException as exc:
            self.db.rollback()
            message = exc.message.replace("该时段已预约", "该时段已被占用")
            return self._record("confirm_reservation", {"success": False, "error": message})
        except IntegrityError:
            self.db.rollback()
            existing = self.db.query(Reservation).filter(
                Reservation.idempotency_key == idempotency_key,
                Reservation.user_id == self.user.id).first()
            if existing:
                return self._record("confirm_reservation", {"success": True,
                    "reservation_id": existing.id, "id": existing.id, "status": existing.status,
                    "idempotent_replay": True})
            return self._record("confirm_reservation", {"success": False, "error": "该时段已占用"})
        if fault_inject.get() == "TOOL_TIMEOUT_AFTER_WRITE":
            self._record("confirm_reservation", {"success": False, "error": "工具响应超时"},
                         timeout_after_write=True)
            raise TimeoutError(idempotency_key)
        return self._record("confirm_reservation", {"success": True, "reservation_id": reservation.id,
            "id": reservation.id, "status": reservation.status, "idempotent_replay": False})

    def get_reservation(self, reservation_id: int | None = None,
                        idempotency_key: str | None = None) -> dict:
        query = self.db.query(Reservation).filter(Reservation.user_id == self.user.id)
        if reservation_id is not None:
            query = query.filter(Reservation.id == reservation_id)
        elif idempotency_key:
            query = query.filter(Reservation.idempotency_key == idempotency_key)
        else:
            return self._record("get_reservation", {"found": False, "error": "缺少查询键"})
        item = query.first()
        return self._record("get_reservation", {"found": bool(item), **({
            "success": True, "reservation_id": item.id, "id": item.id,
            "status": item.status} if item else {})})
