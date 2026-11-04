from datetime import datetime

from sqlalchemy import DateTime, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite") else {},
)

# 数据库session会话的连接工厂
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def ensure_schema_columns(target_engine=engine):
    """轻量旧库升级：只补列/索引，不改表、不删除已有数据。"""
    additions = {
        "users": [("qualifications", "VARCHAR(255) NULL DEFAULT ''")],
        "labs": [("lab_type", "VARCHAR(50) NOT NULL DEFAULT 'general'")],
        "reservations": [("idempotency_key", "VARCHAR(64) NULL")],
        "agent_runs": [("trace_id", "VARCHAR(64) NULL"),
                       ("session_status", "VARCHAR(50) NULL"),
                       ("trace_detail", "TEXT NULL")],
    }
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    with target_engine.begin() as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in present:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        indexes = {item["name"] for item in inspector.get_indexes("agent_runs")} \
            if "agent_runs" in tables else set()
        if "idx_agent_runs_trace_id" not in indexes and "agent_runs" in tables:
            connection.execute(text("CREATE INDEX idx_agent_runs_trace_id ON agent_runs (trace_id)"))
        if "reservations" in tables:
            indexes = {item["name"] for item in inspector.get_indexes("reservations")}
            if "uk_reservation_idempotency" not in indexes:
                connection.execute(text("CREATE UNIQUE INDEX uk_reservation_idempotency ON reservations (idempotency_key)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Base(DeclarativeBase):
    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID", sort_order=-1
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间", sort_order=1
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        comment="更新时间",
        sort_order=2,
    )
