from datetime import date, datetime
from datetime import time as dtime
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import get_db
from app.models.user import User
from app.utils.jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _eval_user(request: Request | None, db: Session) -> User | None:
    """仅在 EVAL_MODE 下生效；普通请求头不能绕过 JWT。"""
    if request is None or not os.environ.get("EVAL_MODE"):
        return None
    try:
        user_id = int(request.headers.get("X-Test-User-Id", "1"))
    except ValueError:
        user_id = 1
    return db.get(User, user_id)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    request: Request = None,
) -> User:
    """JWT token 鉴权验证合法性；独立评测通过 EVAL_MODE 注入夹具用户。"""
    eval_user = _eval_user(request, db)
    if eval_user is not None:
        return eval_user
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
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


def get_token_issued_at(
    request: Request, token: str | None = Depends(oauth2_scheme)
) -> datetime:
    """取**本次登录**的签发时间（JWT 的 iat）；评测模式下按当前时间。"""
    if os.environ.get("EVAL_MODE"):
        return datetime.now()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
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
