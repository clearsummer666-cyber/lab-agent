"""鉴权链路：JWT 签发/校验、过期与篡改、禁用用户、角色权限。"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import BusinessException
from app.config import settings
from app.dependencies.auth import get_current_admin, get_current_user
from app.models.user import User
from app.schemas.user import UserCreateRequest
from app.utils.jwt import create_access_token, decode_access_token
from app.utils.password import hash_password, verify_password


def _encode(payload: dict) -> str:
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


# ───────────────────────── token 本身 ─────────────────────────


def test_token_lifetime_matches_config():
    """回归测试：naive datetime 会让 token 凭空多 8 小时（本机 UTC+8）。

    修复前：配置 24h，实际剩余 32h。根因是 PyJWT 把 naive datetime
    的本地墙上时间当 UTC 处理。
    """
    token = create_access_token(user_id=1)
    payload = decode_access_token(token)

    expected_seconds = settings.JWT_EXPIRE_HOURS * 3600
    actual_seconds = payload["exp"] - datetime.now(timezone.utc).timestamp()

    assert abs(actual_seconds - expected_seconds) < 60


def test_token_carries_user_id():
    payload = decode_access_token(create_access_token(user_id=42))
    assert payload["user_id"] == 42


def test_tampered_token_rejected(db, make_user):
    """改了 payload 但签名对不上 —— 必须 401。"""
    user = make_user()
    token = create_access_token(user.id)
    header, _, signature = token.split(".")
    forged = _encode({"user_id": user.id + 1, "exp": 9999999999})

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=forged, db=db)
    assert exc_info.value.status_code == 401


def test_expired_token_rejected(db, make_user):
    user = make_user()
    expired = _encode(
        {
            "user_id": user.id,
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=expired, db=db)
    assert exc_info.value.status_code == 401


# ───────────────────────── 用户状态 ─────────────────────────


def test_valid_token_returns_user(db, make_user):
    user = make_user()
    current = get_current_user(token=create_access_token(user.id), db=db)
    assert current.id == user.id


def test_token_of_deleted_user_rejected(db):
    """token 合法但用户已被删除。"""
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=create_access_token(99999), db=db)
    assert exc_info.value.status_code == 401


def test_disabled_user_rejected(db, make_user):
    """status=0 的禁用账号，即使 token 没过期也不许进。"""
    user = make_user(status=0)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=create_access_token(user.id), db=db)
    assert exc_info.value.status_code == 401


# ───────────────────────── 角色权限 ─────────────────────────


def test_admin_dependency_accepts_admin(db, make_user):
    admin = make_user(username="admin", role="admin")
    assert get_current_admin(current_user=admin).id == admin.id


def test_admin_dependency_rejects_student(db, make_user):
    """前端藏菜单只是 UX，这里才是真防线。"""
    student = make_user(username="stu", role="student")
    with pytest.raises(BusinessException) as exc_info:
        get_current_admin(current_user=student)
    assert exc_info.value.code == 403


# ───────────────────────── 密码 ─────────────────────────


def test_password_hash_roundtrip():
    hashed = hash_password("123456")
    assert hashed != "123456"
    assert hashed.startswith("$2")
    assert verify_password("123456", hashed)


def test_password_rejects_wrong_input():
    hashed = hash_password("123456")
    assert not verify_password("123457", hashed)
    assert not verify_password("", hashed)


def test_same_password_gets_different_hash():
    """bcrypt 每次加盐，同样的密码不能产生同样的哈希。"""
    assert hash_password("123456") != hash_password("123456")


# ───────────────────────── 唯一约束与必填 ─────────────────────────


def test_duplicate_username_hits_unique_index(db, make_user):
    """账号唯一性不能只靠 service 查重 —— 先查后插之间有竞态窗口。

    并发注册时两个请求都能通过 service 的 `if user: raise`，
    唯一索引 `uk_user_username` 才是真防线。
    """
    make_user(username="alice")

    db.add(User(username="alice", password="x", name="dup", role="student", status=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_create_user_requires_password():
    """密码没有默认值 —— 原来默认 "123"，管理员漏填就批量造弱口令账号。"""
    with pytest.raises(ValidationError):
        UserCreateRequest(username="bob")
