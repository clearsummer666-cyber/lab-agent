"""把黄金语料映射到原项目 Model；仅操作由评测环境指定的 SQLite 数据库。"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.database import Base, engine, ensure_schema_columns
from app.models.agent_run import AgentRun
from app.models.agent_session import AgentSession
from app.models.equipment import Equipment
from app.models.lab import Lab
from app.models.reservation import Reservation
from app.models.reservation_preview import ReservationPreview
from app.models.rule_chunk import RuleChunk
from app.models.user import User
from app.services.kb_service import ingest_rule_corpus
from eval_suite.bootstrap import is_eval_database

DATA_PATH = Path(__file__).with_name("corpus_and_cases.json")


def load_benchmark() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def reset_test_environment(db: Session) -> dict:
    if not is_eval_database(engine):
        raise RuntimeError("拒绝重置非评测数据库")
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns(engine)
    for model in (AgentRun, AgentSession, ReservationPreview, Reservation,
                  Equipment, Lab, RuleChunk, User):
        db.query(model).delete()
    db.commit()
    data = load_benchmark()
    users, slots = {}, {}
    competitor = User(username="eval_competitor", password="eval", name="评测并发对手",
                      role="student", status=1, qualifications="")
    db.add(competitor)
    db.flush()
    for index, item in enumerate(data["initial_fixtures"]["users"], 1):
        user = User(username=item["user_id"], password="eval", name=item["user_id"],
                    role="student", status=1,
                    qualifications=",".join(item["passed_qualifications"]))
        db.add(user)
        db.flush()
        users[item["user_id"]] = user.id
    labs, equipments = {}, {}
    for item in data["initial_fixtures"]["lab_slots"]:
        lab_type = "bio" if item["lab_type"] == "biology" else item["lab_type"]
        lab = labs.get(item["lab_name"])
        if lab is None:
            lab = Lab(name=item["lab_name"], location="评测实验区", capacity=30,
                      open_time="08:00", close_time="22:00", status=1,
                      lab_type=lab_type)
            db.add(lab)
            db.flush()
            labs[item["lab_name"]] = lab
        equipment_key = (lab.id, item["equipment"])
        equipment = equipments.get(equipment_key)
        if equipment is None:
            equipment = Equipment(lab_id=lab.id, name=item["equipment"], quantity=1,
                                  status=1, spec="PCR" if item["equipment"] == "PCR"
                                  else "FUME_HOOD")
            db.add(equipment)
            db.flush()
            equipments[equipment_key] = equipment
        slots[item["slot_id"]] = {"slot_id": f"lab:{lab.id}:equipment:{equipment.id}:{item['date']}:{item['time_range']}",
            "lab_id": lab.id, "equipment_id": equipment.id, "lab_name": lab.name,
            "lab_type": lab.lab_type, "date": item["date"],
            "start_time": item["time_range"].split("-")[0], "end_time": item["time_range"].split("-")[1],
            "time_range": item["time_range"], "status": item["status"],
            "required_rule_codes": item["required_rule_codes"]}
    db.commit()
    counts = ingest_rule_corpus(db, data["rule_corpus"])
    return {"documents": len(data["rule_corpus"]), **counts, "users": len(users),
            "lab_slots": len(slots), "user_ids": users, "slot_map": slots}
