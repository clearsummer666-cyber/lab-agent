"""把 state 里的槽位渲染进 system prompt。

★ 为什么必须有这一层：
  存进 state ≠ 模型看得见。模型只读 messages 和 system。
  不做注入，槽位存了等于白存（第 13 课踩过）。

★ 为什么同步和异步都要实现（L2 接回时踩出来的）：
  AgentMiddleware 只实现 `wrap_model_call` 时，同步 `invoke()` 能跑，
  但 `astream_events()` / `ainvoke()` 会直接抛：
      NotImplementedError: Asynchronous implementation of awrap_model_call is not available.
  原项目的 SSE 走的就是异步路径，所以两个都要写。
  逻辑抽到 `_render()` 里，两个入口共用，不会写重复。
"""

from langchain.agents.middleware import AgentMiddleware


class SlotsMiddleware(AgentMiddleware):
    """每次调模型前，把已知槽位追加到 system prompt 末尾。"""

    @staticmethod
    def _render(request) -> str | None:
        """算出要注入的完整 prompt。没有槽位就返回 None（不用改）。"""
        slots = request.state.get("slots") or {}
        if not slots:
            return None
        base = request.system_message.content if request.system_message else ""
        lines = "\n".join(f"- {k}：{v}" for k, v in slots.items())
        return f"{base}\n\n【已知预约信息，问过就别再问】\n{lines}"

    def wrap_model_call(self, request, handler):
        prompt = self._render(request)
        if prompt is None:
            return handler(request)
        return handler(request.override(system_prompt=prompt))

    async def awrap_model_call(self, request, handler):
        prompt = self._render(request)
        if prompt is None:
            return await handler(request)
        return await handler(request.override(system_prompt=prompt))
