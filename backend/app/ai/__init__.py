"""lab-ai 的 Agent 能力，接到原项目里。

来源：`E:/AI_Study/Course/ai对话/lab-ai/agent/`，为接原项目做了三处适配：
    1. repo 只留 SqlRepo（真库），MemoryRepo 留在 lab-ai 给评测用
    2. 不读 lab-ai 的 .env，用原项目的 settings
    3. require_confirm=False（前端无确认弹窗，见 L2-PLAN 决策 D4）

对外只用两个：
    from app.ai import build_agent, thread_id_of
"""

import os

# ★ 必须在 kb_service 加载 embedding 模型之前设，且必须放在这个文件。
#   模型本地已有缓存（~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5），
#   但 huggingface_hub 默认仍会联网校验，走代理会 502 Bad Gateway，
#   表现成「RAG 一直卡住不返回」。
#
#   为什么放这里而不是 agent_service.py：
#   那个文件只在 HTTP 路由被 import 时执行。直接 `from app.ai import build_agent`
#   的脚本（比如评测、调试）不经过它，环境变量就失效了 —— 实测踩过。
#   `app/ai/__init__.py` 是所有入口的必经之路。
#   （kb_service.py 本身不在可改范围，见 L2-PLAN 目录责任表）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app.ai.builder import build_agent, thread_id_of  # noqa: E402
from app.ai.repo import SqlRepo  # noqa: E402
from app.ai.state import BookingState  # noqa: E402
from app.ai.tools import make_tools  # noqa: E402

__all__ = ["build_agent", "thread_id_of", "SqlRepo", "BookingState", "make_tools"]
