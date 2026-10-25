from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings


def create_access_token(user_id: int) -> str:
    """创建JWT token"""
    # 必须用带时区的 UTC 时间：naive datetime 会被 PyJWT 当作 UTC 处理，
    # 而本机是 UTC+8，实际寿命会凭空多出 8 小时（配置 24h → 实测 32h）。
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)

    payload = {"user_id": user_id, "exp": expire}  # exp是固定key，不能写成别的

    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str) -> dict:
    """解析jwt token"""
    return jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
