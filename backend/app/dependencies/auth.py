from datetime import date, datetime
from datetime import time as dtime

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import get_db
from app.models.user import User
from app.utils.jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """JWT token 鉴权验证合法性"""
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    # 获取到用户ID
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证"
        )
    # 从数据库根据用户ID查询用户信息
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(401, "用户不存在")
    if user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户被禁用"
        )
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """只有管理员才能访问"""
    if current_user.role != "admin":
        raise BusinessException(message="无权限访问", code=403)
    return current_user


def get_token_issued_at(token: str = Depends(oauth2_scheme)) -> datetime:
    """取**本次登录**的签发时间（JWT 的 iat）。

    AI 额度按「登录会话」计：重新登录 → 新 token → 新的 iat → 额度自动刷新。
    这样测试时反复登录就能重置，不用等第二天。

    注意返回的是**本地时区的 naive datetime**，因为数据库里的 create_time
    是 `datetime.now()` 写的本地 naive 值，两边必须同口径才能比大小。
    """
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    iat = payload.get("iat")
    if not iat:
        # 兼容改造前签发、还没过期的旧 token（它们没有 iat）：
        # 退化成「今天 0 点」的旧口径，避免老 token 一用就报错
        return datetime.combine(date.today(), dtime.min)
    # iat 是 UTC 时间戳 → fromtimestamp 会转成本地时间
    return datetime.fromtimestamp(int(iat))
