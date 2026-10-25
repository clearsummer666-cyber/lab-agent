"""pytest 公共配置。

**关键**：必须在 import 任何 app 模块之前把 DATABASE_URL 指向临时 SQLite。
`app/database.py` 在模块导入时就执行 `create_engine(settings.DATABASE_URL)`，
晚一步就只能连真 MySQL —— 测试不该依赖本机数据库。

为什么用 SQLite 而不是 MySQL：
- 业务校验逻辑（区间重叠、状态机、容量）是纯 Python + SQL 条件，与方言无关；
- 唯一索引、外键、事务在 SQLite 上同样生效，能验证「兜底防线」；
- 代价：SQLite 会忽略 `SELECT ... FOR UPDATE`，所以**悲观锁本身测不了**，
  并发用例请连 MySQL 跑（见 `test_concurrency.py` 的 skipif）。
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="labpilot-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-do-not-use-in-prod")
os.environ.setdefault("JWT_EXPIRE_HOURS", "24")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ["HF_HUB_OFFLINE"] = "1"

# 模型配置：测试不调模型，但 Settings 有 fail-fast 校验，必须给一套值。
# 显式清空 AI_LLM_*，让测试只认下面这套假的，绝不读 .env 里的真实 key。
for _key in ("AI_LLM_MODEL", "AI_LLM_BASE_URL", "AI_LLM_API_KEY"):
    os.environ[_key] = ""
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("LLM_API_KEY", "test-key")

import pytest  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, engine  # noqa: E402

# 必须 import 所有模型，create_all 才认得这些表
from app.models.equipment import Equipment  # noqa: E402,F401
from app.models.lab import Lab  # noqa: E402
from app.models.reservation import Reservation  # noqa: E402,F401
from app.models.user import User  # noqa: E402


@pytest.fixture
def db() -> Session:
    """每个用例一套干净的表。

    SQLite 建表是毫秒级，直接 drop + create 比维护事务嵌套简单得多，
    也避免 service 里 `db.commit()` / `db.rollback()` 把外层事务弄坏。
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine, autoflush=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_user(db):
    """造用户。注意 users 表的 name / role 是 NOT NULL。"""

    def _make(username: str = "stu1", role: str = "student", status: int = 1) -> User:
        user = User(
            username=username,
            password="not-a-real-hash",
            name=username,
            role=role,
            status=status,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture
def make_lab(db):
    """造实验室。默认 08:00-22:00 开放、30 人容量。"""

    def _make(
        name: str = "实验室A",
        capacity: int = 30,
        open_time: str | None = "08:00",
        close_time: str | None = "22:00",
        status: int = 1,
    ) -> Lab:
        lab = Lab(
            name=name,
            location="实验楼 301",
            capacity=capacity,
            open_time=open_time,
            close_time=close_time,
            status=status,
        )
        db.add(lab)
        db.commit()
        db.refresh(lab)
        return lab

    return _make


@pytest.fixture
def make_equipment(db, make_lab):
    def _make(lab: Lab, name: str = "示波器", status: int = 1) -> Equipment:
        equipment = Equipment(lab_id=lab.id, name=name, quantity=1, status=status)
        db.add(equipment)
        db.commit()
        db.refresh(equipment)
        return equipment

    return _make


@pytest.fixture
def make_reservation(db):
    """直接落库造预约，用于绕过 service 构造特定状态（status=1/2/3）。"""

    def _make(
        user: User,
        lab: Lab,
        date: str,
        start_time: str = "10:00",
        end_time: str = "12:00",
        status: int = 0,
        equipment_id: int | None = None,
    ) -> Reservation:
        item = Reservation(
            user_id=user.id,
            lab_id=lab.id,
            equipment_id=equipment_id,
            date=date,
            start_time=start_time,
            end_time=end_time,
            status=status,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    return _make


@pytest.fixture
def tomorrow() -> str:
    """明天。用未来日期，绕开「不能约过去」的校验。"""
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.fixture
def yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
