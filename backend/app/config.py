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

    # ── AI 用量限额（公网演示，防止被人刷爆 token）──
    # 口径：**按登录会话**计，不是按天。重新登录 → 新 token（带新的 iat）→ 额度自动刷新。
    #       这样测试时反复登录就能重置，不用等第二天。
    # 实测：一次完整预约（3 轮对话）约 12,400 token，其中输入占大头
    #       —— system prompt + 9 个工具 schema 每轮都要重发，这是 Agent 架构的固有成本。
    # 都能在 .env 里覆盖；改成 0 表示该项不限。
    AI_SESSION_RUN_LIMIT: int = 10        # 单次登录最多几轮对话
    AI_SESSION_TOKEN_LIMIT: int = 25000   # 单次登录最多多少 token（≈2 次完整预约）

    # 兜底日限额：**登录不清零**。只防「反复重登无限刷」，正常演示撞不到。
    # 嫌麻烦就设 0 关掉。
    AI_DAILY_RUN_LIMIT: int = 80
    AI_DAILY_TOKEN_LIMIT: int = 200000

    AI_MAX_INPUT_CHARS: int = 500       # 单条消息最长多少字（防一次塞 5000 字顶爆输入）

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
