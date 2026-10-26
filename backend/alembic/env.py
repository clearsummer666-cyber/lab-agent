"""Alembic 运行环境。

URL 从 `app.config.settings.DATABASE_URL` 读，**不在 alembic.ini 里写死** ——
否则改一次数据库要同步改两处，还容易把密码提交进 git。

常用命令（都在 backend/ 下执行）：
    alembic upgrade head                        # 升到最新
    alembic revision --autogenerate -m "说明"   # 按模型改动生成迁移
    alembic downgrade -1                        # 回退一步
    alembic current / history                   # 看当前版本 / 历史
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic 一定能 import 到 app 包（从别的目录调用时也能跑）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# 必须 import 所有模型，autogenerate 才认得这些表
from app.models.agent_run import AgentRun  # noqa: E402,F401
from app.models.equipment import Equipment  # noqa: E402,F401
from app.models.lab import Lab  # noqa: E402,F401
from app.models.reservation import Reservation  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 密码里若有 % 会被 ConfigParser 当插值符，转义一下
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # 字段类型变化也纳入 autogenerate
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
