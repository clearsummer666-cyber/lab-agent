"""顶层兼容命名空间：把 `app.*` 直接解析到原项目 backend/app。"""
from pathlib import Path

_BACKEND_APP = Path(__file__).resolve().parent.parent / "backend" / "app"
__path__.append(str(_BACKEND_APP))
