from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services import auth as auth_service


router = APIRouter(prefix="/auth", tags=["权限验证"])


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    result = auth_service.login(db, data)
    return Response.success(message="登录成功", data=result)


@router.post("/register")
def register(
    data: RegisterRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),  # 2026-10-26：公网部署，关闭匿名注册
):
    """建账号 —— **仅管理员**。

    原来这个接口是公开的：任何人调一次就多一个账号，
    配合按用户计的 AI 额度，等于「注册小号就能无限续杯」，限额形同虚设。
    管理员建用户走的是 `/api/user`（User.vue 里用的），这个接口保留给脚本/接口调用。
    """
    auth_service.register(db, data)
    return Response.success(message="注册成功")
