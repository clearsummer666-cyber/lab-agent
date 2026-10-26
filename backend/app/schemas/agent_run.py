from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentRunItem(BaseModel):
    """调用日志列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    thread_id: str
    question: str
    status: int
    duration_ms: int
    model_ms: int = 0
    tool_ms: int = 0
    tool_count: int
    tool_calls: str
    input_tokens: int
    output_tokens: int
    error: str | None = None
    create_time: datetime


class AgentRunDetail(AgentRunItem):
    """详情：目前字段与列表项一致，单独留一个类方便以后加事件流。"""


class DailyStat(BaseModel):
    date: str
    total: int
    failed: int
    tokens: int


class ToolStat(BaseModel):
    name: str
    count: int


class AgentStats(BaseModel):
    """运营后台顶部的四张卡 + 两张图。"""

    total: int = 0
    success: int = 0
    failed: int = 0
    success_rate: float = 0.0
    avg_duration_ms: int = 0
    avg_model_ms: int = 0
    avg_tool_ms: int = 0
    total_tokens: int = 0
    avg_tokens: int = 0
    by_day: list[DailyStat] = []
    top_tools: list[ToolStat] = []
