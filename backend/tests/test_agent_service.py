"""stream_agent 的纯函数层：token 事件的节点过滤。

不调真模型——astream_events 的事件就是 dict，直接喂给判定函数。
"""

import re

from app.ai import session
from app.services import agent_service


def test_token_from_tools_node_is_blocked():
    """tools 节点冒出的模型流式输出是内部噪声，必须挡掉。"""
    event = {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "tools"}}
    assert agent_service._should_emit_token(event) is False


def test_token_from_unknown_or_missing_node_passes():
    """黑名单制：节点名缺失/未知时放行——宁多吐一个碎片不吞正文。"""
    assert agent_service._should_emit_token({}) is True
    assert agent_service._should_emit_token({"metadata": {}}) is True
    assert (
        agent_service._should_emit_token({"metadata": {"langgraph_node": "agent"}})
        is True
    )


def test_reset_session_increments_thread():
    """reset_session 强制开新会话：连调两次，thread_id 形如 user:{id}:s{n} 且 n 递增。

    直接写真实 backend/data/threads.db，用独特的 user_id 避免污染其他数据。
    """
    user_id = 987654
    first = session.reset_session(user_id)
    second = session.reset_session(user_id)

    m1 = re.fullmatch(r"user:987654:s(\d+)", first)
    m2 = re.fullmatch(r"user:987654:s(\d+)", second)
    assert m1 and m2
    assert int(m2.group(1)) == int(m1.group(1)) + 1
