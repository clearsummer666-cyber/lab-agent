from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # 后端项目的根路径

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip",
}


class Settings(BaseSettings):
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # ── 模型配置：两套，都在同一个 .env 里 ──
    # AI_LLM_* 优先。用途：业务兜底模型可能撞免费额度的速率限制（实测 429），
    # 这时只换 AI_LLM_* 就能把对话切到别的模型，不用动业务侧配置。
    # 2026-10-25 合并：原来这部分单独放 backend/.env.ai，多一个文件容易漏配。
    AI_LLM_MODEL: str = ""
    AI_LLM_BASE_URL: str = ""
    AI_LLM_API_KEY: str = ""

    # 兜底模型（原项目配置）
    LLM_MODEL: str = ""
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8"
    )

    @model_validator(mode="after")
    def _require_llm_config(self):
        """至少配齐一套模型三件套，缺了在启动时就炸 —— 别等到第一个用户提问。"""
        has_ai = all((self.AI_LLM_MODEL, self.AI_LLM_BASE_URL, self.AI_LLM_API_KEY))
        has_fallback = all((self.LLM_MODEL, self.LLM_BASE_URL, self.LLM_API_KEY))
        if not (has_ai or has_fallback):
            raise ValueError(
                "模型配置缺失：backend/.env 里必须配齐 AI_LLM_* 或 LLM_* 三件套"
            )
        return self


settings = Settings()
