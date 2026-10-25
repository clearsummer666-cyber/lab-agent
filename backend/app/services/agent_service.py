"""AI 对话服务。

**对外接口一个字没变**（run_agent / stream_agent / TOOL_LABELS），
内部实现换成了 `app.ai`（来源：`ai对话/lab-ai/agent/`）。

换掉的四件事：
    1. 手搓 `StateGraph` + `ToolNode`  →  `create_agent`（带 middleware）
    2. 无状态（前端传完整历史）        →  checkpointer + `thread_id = user:{id}`
    3. 工具直接操作 db                 →  `SqlRepo` 抽象
    4. 模型"齐了就提交"                →  prompt 约束 + 数据库幂等

**没换的**：SSE 六种事件（status / tool_start / tool_end / token / done / error）、
响应格式、`recursion_limit` 的思路。
"""

# 离线模式的环境变量在 app/ai/__init__.py 里设（所有入口的必经之路）。
# 放在本文件不可靠：直接 import app.ai 的脚本不经过这里。
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.orm import Session

from app.ai import build_agent as ai_build_agent
from app.ai.builder import get_async_checkpointer
from app.ai import thread_id_of
from app.ai.retention import maybe_purge, touch
from app.ai.router import pick_tool_names
from app.common.exceptions import BusinessException
from app.models.user import User
from app.schemas.ai import ChatRequest

# 工具英文名 → 页面上给人看的中文过程文案
# ★ 前端按 name 匹配这个表，所以工具名不能改，只能往里加条目
TOOL_LABELS = {
    "search_lab_docs": "检索实验室知识库",
    "list_open_labs": "查询开放实验室",
    "list_lab_equipments": "查询实验室设备",
    "list_availability": "查询空闲时段",
    "create_lab_reservation": "提交预约",
    "get_today": "获取今天日期",
    "list_my_reservations": "查询我的预约",
    "cancel_my_reservation": "取消预约",
    "save_slot": "记录预约信息",
}

# agent ⇄ tools 来回上限，防空转。
#
# 15 → 25（2026-10-25）：实测 LA09「设备预约」会撞上限。
# 那条任务要串 4 个工具（查实验室 → 查空闲 → 查设备 → 提交），
# 每次工具调用吃 2 步（AI 消息 + Tool 结果），光正常路径就要 8-9 步，
# 模型只要有一次参数重试（设备 id 传错、时间格式重来）就超过 15：
#
#     langgraph.errors.GraphRecursionError: Recursion limit of 15 reached
#
# 25 是 LangGraph 自己的默认值。原项目收到 15 是为了防死循环，
# 但工具从 7 个涨到 9 个、又多了一个 4 工具串联的流程，15 已经不够。
RECURSION_LIMIT = 25


def _last_user_text(data: ChatRequest) -> str:
    """只取最后一条 user 消息（L2-PLAN 决策 D3）。

    前端还在传完整历史，但**历史以 checkpointer 为准**——这是整个重构的意义。
    继续用前端历史的话，用户还是能伪造自己说过的话，刷新还是失忆。
    """
    for message in reversed(data.messages or []):
        if message.role == "user" and (message.content or "").strip():
            return message.content.strip()
    raise BusinessException(message="请输入您要对话的内容")


def _config(current_user: User) -> dict:
    return {
        "configurable": {"thread_id": thread_id_of(current_user)},
        "recursion_limit": RECURSION_LIMIT,
    }


def _text_of(content: Any) -> str:
    """模型返回的 content 可能是 str，也可能是 [{type,text}] 块列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content) if content is not None else ""


async def run_agent(db: Session, current_user: User, data: ChatRequest):
    """非流式对话。

    ★ 改成 async 了：checkpointer 用 AsyncSqliteSaver，
      同步的 SqliteSaver 不支持 await，流式接口会直接抛
      NotImplementedError（踩过）。
    """
    text = _last_user_text(data)
    saver = await get_async_checkpointer()
    touch(thread_id_of(current_user))
    maybe_purge(saver)          # 每 100 次才真扫一次，见 app/ai/retention.py
    agent = ai_build_agent(
        db, current_user, checkpointer=saver, tool_names=pick_tool_names(text)
    )
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=text)]}, config=_config(current_user)
        )
    except BusinessException:
        raise
    except Exception as exc:                           # noqa: BLE001
        logging.exception("[ai] 非流式对话异常")
        raise BusinessException(message="大模型调用失败，请稍后重试") from exc

    messages = result.get("messages") or []
    if not messages:
        raise BusinessException(message="大模型没有任何返回内容")
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = _text_of(message.content).strip()
            if content:
                return content
    raise BusinessException(message="大模型没有任何返回内容")


def _chunk_text(chunk: Any) -> str:
    """从模型流式 chunk 里抠出纯文本。"""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content) if content is not None else ""


def _tool_output_preview(output: Any, limit: int = 200) -> str:
    """把工具结果收成短预览，避免整段 JSON 刷到前端过程区。"""
    if isinstance(output, ToolMessage):
        output = output.content
    text = output if isinstance(output, str) else str(output)
    return text if len(text) <= limit else text[:limit] + "…"


async def stream_agent(
    db: Session, current_user: User, data: ChatRequest
) -> AsyncIterator[dict]:
    """生成器：边跑 Agent 边 yield 事件，供 SSE 推给前端。

    事件类型：status / tool_start / tool_end / token / done / error
    ★ 任何错误都必须 yield，不能 raise —— 一旦 raise，SSE 已经开了头，
      前端只会看到连接断开。
    """
    try:
        text = _last_user_text(data)
        saver = await get_async_checkpointer()
        touch(thread_id_of(current_user))
        maybe_purge(saver)
        agent = ai_build_agent(
            db, current_user, checkpointer=saver, tool_names=pick_tool_names(text)
        )
        inputs = {"messages": [HumanMessage(content=text)]}

        yield {"type": "status", "message": "正在思考…"}

        # token 埋点：SSE 层原本拿不到用量，评测/成本监控都是瞎的。
        # 在 done 事件里带一个 usage 字段 —— **只加不改**，前端只读 type，
        # 多出来的字段会被忽略，所以不破坏冻结契约。
        usage_in = usage_out = 0

        async for event in agent.astream_events(
            inputs, version="v2", config=_config(current_user)
        ):
            kind = event.get("event")
            if kind == "on_tool_start":
                name = event.get("name") or ""
                yield {
                    "type": "tool_start",
                    "name": name,
                    "label": TOOL_LABELS.get(name, name),
                }
            elif kind == "on_tool_end":
                name = event.get("name") or ""
                yield {
                    "type": "tool_end",
                    "name": name,
                    "label": TOOL_LABELS.get(name, name),
                    "preview": _tool_output_preview(
                        (event.get("data") or {}).get("output")
                    ),
                }
            elif kind == "on_chat_model_end":
                msg = (event.get("data") or {}).get("output")
                um = getattr(msg, "usage_metadata", None) or {}
                usage_in += um.get("input_tokens", 0) or 0
                usage_out += um.get("output_tokens", 0) or 0
            elif kind == "on_chat_model_stream":
                text_chunk = _chunk_text((event.get("data") or {}).get("chunk"))
                if text_chunk:
                    yield {"type": "token", "content": text_chunk}

        yield {
            "type": "done",
            "usage": {"input": usage_in, "output": usage_out,
                      "total": usage_in + usage_out},
        }
    except BusinessException as exc:
        yield {"type": "error", "message": exc.message}
    except Exception:                                  # noqa: BLE001
        # 流式接口不能 raise（SSE 已经开头了），但异常必须留痕，
        # 否则前端只看到「大模型调用失败」，排查时完全没有线索。
        logging.exception("[ai] 流式对话异常")
        yield {"type": "error", "message": "大模型调用失败，请稍后重试"}
