"""上下文裁剪中间件。

问题：对话轮数一多，历史无限增长，每轮都全量重发给模型。
      9 个工具的 schema + 完整历史，实测单条用例 21K–33K 输入 token。

做法：调模型前按 token 预算裁一遍，但**三类内容永不裁**：
    1. SystemMessage
    2. 第一条 HumanMessage（任务目标）
    3. 用户明确说「记住」的消息

★ 关键：`request.override(messages=...)` 只换**给模型看的那份**，
   checkpointer 里存的完整历史一个字不动。所以裁剪是可逆的、不丢数据。

★ 同步和异步都要实现（这个坑第 17 课踩过）：
   只写 `wrap_model_call` 时，同步 invoke() 能跑，
   但原项目的 SSE 走 `astream_events()`，会抛
   NotImplementedError: awrap_model_call is not available。
"""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages.utils import count_tokens_approximately

from app.ai.memory_utils import CHARS_PER_TOKEN, pinned_trim

# 历史部分的 token 预算。不含工具 schema 和 system prompt（那些是固定开销）。
# 6000 大约够 15-20 轮正常对话；超出的从最老的开始丢。
DEFAULT_MAX_TOKENS = 6000


class TrimMiddleware(AgentMiddleware):
    """保护式裁剪：锚点永不丢，剩余预算从后往前填。"""

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS):
        super().__init__()
        self.max_tokens = max_tokens
        # 最近一次裁剪前后的 token 数，排查时看这个
        self.last_before = 0
        self.last_after = 0

    def _trim(self, request):
        if not request.messages:
            return request
        self.last_before = count_tokens_approximately(
            request.messages, chars_per_token=CHARS_PER_TOKEN
        )
        trimmed = pinned_trim(request.messages, self.max_tokens)
        self.last_after = count_tokens_approximately(
            trimmed, chars_per_token=CHARS_PER_TOKEN
        )
        if len(trimmed) == len(request.messages):
            return request                      # 没裁掉任何东西，不用 override
        return request.override(messages=trimmed)

    def wrap_model_call(self, request, handler):
        return handler(self._trim(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._trim(request))
