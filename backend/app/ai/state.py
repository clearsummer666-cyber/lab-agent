"""Agent 的状态定义。

`AgentState` 自带 `messages`，我们只加一个 `slots`。

为什么槽位不放在 messages 里：历史一旦被裁剪就丢了。
放进 state，历史清到 0 条，需求还在（第 13 课实测）。

★ 为什么 slots 要挂 reducer（第 17 课踩出来的）：
  模型会在**同一轮里并行调多次** `save_slot`（一次存五个字段）。
  每个工具都返回 `Command(update={"slots": ...})`，
  LangGraph 会报：
      InvalidUpdateError: At key 'slots': Can receive only one value per step.
  加一个合并函数就好了。默认行为是「一个 step 只能写一次」，这对
  标量字段合理，对「累积型」字段（dict / list）就必须自己定合并规则。
"""

from typing import Annotated

from langchain.agents.middleware import AgentState


def merge_slots(left: dict | None, right: dict | None) -> dict:
    """把这一 step 的多次写入合并起来。

    约定：**传空 dict 表示清空**（提交成功后槽位退场）。
    业务代码里空 dict 只用于清空，所以这样判是安全的。
    """
    if not right:
        return {}
    return {**(left or {}), **(right or {})}


class BookingState(AgentState):
    """预约 Agent 的状态。

    messages  对话历史（框架自带）
    slots     已抽到的预约字段，键见 tools.SLOT_KEYS
    """

    slots: Annotated[dict, merge_slots]
