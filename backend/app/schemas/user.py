from pydantic import BaseModel, ConfigDict

class UserResponse(BaseModel):
    id: int
    username: str
    name: str
    role: str
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    status: int

    model_config = ConfigDict(from_attributes=True)

class UserCreateRequest(BaseModel):
    username: str
    # 不设默认值：原来默认 "123"，管理员漏填就批量造出弱口令账号。
    # 前端 User.vue 的密码字段本来就是 required，这里对齐成必填。
    password: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    role: str = "student"
    status: int = 1

class UserUpdateRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    role: str | None = None
    status: int | None = None

class PasswordUpdateRequest(BaseModel):
    old_password: str
    new_password: str
