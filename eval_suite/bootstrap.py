"""评测环境显式引导。

本模块导入时**不产生副作用**；runner/pytest 必须在导入 `app.database`
之前调用 `configure_eval_environment()`。这样生产服务即使运行中被导入
评测包，也不会改写环境变量或删除正在使用的数据库文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
EVAL_DB = ROOT / "data" / "lab_eval.db"
EVAL_THREAD_DB = ROOT / "data" / "eval_threads.db"

_configured = False


def _same_path(value: str | None, expected: Path) -> bool:
    if not value:
        return False
    try:
        return Path(value).resolve() == expected.resolve()
    except (OSError, ValueError):
        return False


def _safe_unlink(path: Path) -> None:
    """只清理可再生评测库；文件被占用时保持原样，不打断当前连接。"""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except PermissionError:
            # Windows 下服务仍持有句柄时会出现；此路径只在 app 导入前调用，
            # 捕获后由 fixtures 清表，绝不冒删库风险。
            continue


def configure_eval_environment() -> None:
    """在 app.database 首次导入前固定独立 SQLite 与评测开关。

    重复调用幂等。若 app.database 已经加载且指向的不是评测库，直接报错，
    避免评测脚本误把生产库当夹具重置。
    """
    global _configured
    if _configured:
        return

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    if "app.database" in sys.modules:
        current = os.environ.get("DATABASE_URL", "")
        if _same_path(current, EVAL_DB):
            _configured = True
            return
        raise RuntimeError("评测环境必须在导入 app.database 之前配置")

    EVAL_DB.parent.mkdir(parents=True, exist_ok=True)
    _safe_unlink(EVAL_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{EVAL_DB.as_posix()}"
    os.environ["EVAL_MODE"] = "1"
    os.environ["EVAL_THREAD_DB_PATH"] = str(EVAL_THREAD_DB)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("LLM_MODEL", "deterministic-eval")
    os.environ.setdefault("LLM_BASE_URL", "http://localhost:1/v1")
    os.environ.setdefault("LLM_API_KEY", "not-used")
    os.environ.setdefault("JWT_SECRET_KEY", "eval-only-secret")
    _configured = True


def is_eval_database(engine) -> bool:
    """判断当前引擎是否确实指向独立评测库。"""
    database = getattr(getattr(engine, "url", None), "database", None)
    return _same_path(database, EVAL_DB)
