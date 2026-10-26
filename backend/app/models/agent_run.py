from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentRun(Base):
    """Agent 单次调用记录 —— 运营后台的数据源。

    定位是**旁路**：写失败绝不影响对话本身。
    所以这里的字段都是"事后统计用"，不参与任何业务判断。

    为什么单独建表而不是写日志文件：
    - 后台要按天 / 按用户 / 按状态聚合（GROUP BY），文件做不到
    - 要能分页查询、点开看详情，文件还得自己解析
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        # 统计接口一定带时间范围过滤，所以 create_time 必须有索引
        Index("idx_agent_runs_create_time", "create_time"),
        Index("idx_agent_runs_user_id", "user_id"),
        {"comment": "Agent 调用日志"},
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), comment="调用人", nullable=False
    )
    thread_id: Mapped[str] = mapped_column(
        String(64), comment="会话ID（user:{id}:s{n}）", nullable=False
    )
    question: Mapped[str] = mapped_column(
        String(500), comment="用户输入，超长截断", nullable=False
    )
    status: Mapped[int] = mapped_column(
        comment="0 成功，1 失败", default=0, nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(
        comment="本轮总耗时（毫秒）", default=0, nullable=False
    )
    model_ms: Mapped[int] = mapped_column(
        comment="模型调用累计耗时（毫秒）", default=0, nullable=False
    )
    tool_ms: Mapped[int] = mapped_column(
        comment="工具执行累计耗时（毫秒）", default=0, nullable=False
    )
    tool_calls: Mapped[str] = mapped_column(
        String(1000), comment="工具调用序列，逗号分隔", default="", nullable=False
    )
    tool_count: Mapped[int] = mapped_column(
        comment="工具调用次数", default=0, nullable=False
    )
    input_tokens: Mapped[int] = mapped_column(
        comment="输入 token", default=0, nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        comment="输出 token", default=0, nullable=False
    )
    error: Mapped[str] = mapped_column(
        String(255), comment="失败原因", nullable=True
    )
