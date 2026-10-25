from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # 账号唯一。service 层查重有竞态窗口（先查后插之间），
        # 唯一索引才是真防线 —— 否则并发注册能插进两个同名账号，
        # 登录时 first() 随机命中其中一个。
        UniqueConstraint("username", name="uk_user_username"),
        {"comment": "用户信息表"},
    )

    username: Mapped[str] = mapped_column(
        String(50), comment="账号", nullable=False
    )
    password: Mapped[str] = mapped_column(String(255), comment="密码", nullable=False)
    name: Mapped[str] = mapped_column(String(50), comment="名称", nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), comment="角色：student-学生，admin-管理员", nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(50), comment="邮箱")
    phone: Mapped[str | None] = mapped_column(String(50), comment="手机号")
    avatar: Mapped[str | None] = mapped_column(String(50), comment="头像")
    status: Mapped[int] = mapped_column(default=1, comment="状态：0-禁用，1-正常")
