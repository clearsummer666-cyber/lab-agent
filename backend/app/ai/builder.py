"""组装 Agent。用原项目的模型配置和数据库。

和 `lab-ai/agent/builder.py` 的区别：
    1. 不读 lab-ai 的 `.env`，用原项目的 `app.config.settings`
    2. `repo` 固定是 SqlRepo（接真库）
    3. `require_confirm=False` —— 原项目前端没有确认弹窗，
       搬 interrupt 会让 SSE 永久卡在「正在思考…」（L2-PLAN 决策 D4 方案 A）
    4. checkpointer 是**跨请求共享**的单例：thread_id 用 `user:{id}`，
       历史要跨请求活下来，每次新建就白存了
"""

import datetime

import os
import sqlite3
from pathlib import Path

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.ai.middleware import SlotsMiddleware
from app.ai.repo import SqlRepo
from app.ai.state import BookingState
from app.ai.trim import TrimMiddleware
from app.ai.tools import make_tools
from app.config import settings

# ★ 持久化 checkpointer：进程重启后对话历史还在。
#   放 data/checkpoints.sqlite，和 chroma / kb 同级，不污染代码目录。
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "checkpoints.sqlite"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ★ 同步 / 异步必须分开（踩过）：
#   SqliteSaver 只有同步方法，流式接口走 astream_events 会抛
#   NotImplementedError: The SqliteSaver does not support async methods.
#   所以同步用 SqliteSaver，异步用 AsyncSqliteSaver，指向同一个文件。
#   check_same_thread=False：FastAPI 多线程会复用连接。
_conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
_SAVER = SqliteSaver(_conn)

_async_saver = None


async def get_async_checkpointer():
    """异步场景（stream_agent / ainvoke）用这个。

    延迟初始化：aiosqlite.connect 是 async，模块级不能 await。
    """
    global _async_saver
    if _async_saver is None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(str(_DB_PATH))
        _async_saver = AsyncSqliteSaver(conn)
    return _async_saver

SYSTEM_PROMPT = """你是 LabPilot 实验室智能体，回答要简洁。今天是 {today}。

你可以：
1. 用 search_lab_docs 查询实验室的规则、安全、开放时间等问题
2. 用 list_open_labs / list_lab_equipments 查询真实的实验室和设备（拿 lab_id）
3. 用 list_availability 查询某天某实验室的空闲时段
4. 用 get_today 换算日期
5. 用 create_lab_reservation 提交预约（落库，状态待审核）
6. 用 list_my_reservations 查询当前用户的预约记录
7. 在用户确认后用 cancel_my_reservation 取消预约

## 必须遵守
- 用户提到 今天、明天、后天 等日期，请先调用 get_today 获取日期，**不要凭空猜测**
- **提交前必须先复述**：实验室名称、日期、开始时间、结束时间、人数
- **复述之后，必须等用户在下一条消息里明确回复「确认」「确认预约」「就这样」「提交吧」之类的词，才能调用 create_lab_reservation**
- ⚠️ 即使用户在**第一条**消息里就把所有信息说全了（例如「帮我约明天下午2点化学实验室4个人」），
  也**只能先复述并询问是否确认**，**绝对不能直接提交**。
  「帮我约…」是发起请求，不是确认。
- 缺 lab_id 时先用 list_open_labs 查到，不要凭空创建
- 预约成功后状态是待审核，必须管理员确认后才能使用——这点要告诉用户
- 用户还没定时间、只是打听，先给空闲情况，不要催着填表
- 不要瞎编数据库里没有的实验室或者设备信息
- 如果问开放时间或者实验室规则，优先调用 search_lab_docs，不要凭空回复
"""


def _llm_config() -> dict:
    """模型配置。优先 `backend/.env.ai`，回落到原项目 settings。

    ★ 为什么单独一个配置文件：
      原 `.env` 里的 `LLM_*` 是原项目的配置，不在可改范围（L2-PLAN 目录责任表）。
      但它的 agnes 免费 key 会限流（实测 429 速率限制），换模型又不能改它。
      所以新建 `.env.ai` 放 `AI_LLM_*`，用 dotenv 读进环境变量后优先使用。
      原 `.env` 一个字没动。
    """
    from dotenv import load_dotenv

    env_file = Path(__file__).resolve().parent.parent.parent / ".env.ai"
    if env_file.exists():
        load_dotenv(env_file, override=False)   # 已有环境变量优先，不硬覆盖

    return {
        "model": os.getenv("AI_LLM_MODEL") or settings.LLM_MODEL,
        "base_url": os.getenv("AI_LLM_BASE_URL") or settings.LLM_BASE_URL,
        "api_key": os.getenv("AI_LLM_API_KEY") or settings.LLM_API_KEY,
    }


def build_agent(db, current_user, llm=None, checkpointer=None,
                max_history_tokens: int | None = None,
                tool_names: list[str] | None = None):
    """给原项目用的 Agent 入口。

    db / current_user 从 FastAPI 依赖注入拿，**闭包捕获，不进 state**
    —— state 要被 checkpointer 序列化，塞 ORM 对象会炸（L2-PLAN 第八节）。

    max_history_tokens  历史裁剪预算。不传用 TrimMiddleware 的默认值（6000）。
                        调小可以强制触发裁剪，验证行为。
    tool_names      只挂这些工具。不传挂全集（9 个）。
                    见 app/ai/router.py 的 pick_tool_names()，按意图裁剪 schema 成本。
    """
    repo = SqlRepo(db, current_user.id)

    if llm is not None:
        model = llm
    else:
        cfg = _llm_config()
        model = ChatOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=cfg["model"],
            temperature=0,
            streaming=True,
        )

    tools = make_tools(store=None, repo=repo, require_confirm=False)
    if tool_names:
        wanted = set(tool_names)
        tools = [t for t in tools if t.name in wanted]

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT.format(today=datetime.date.today()),
        state_schema=BookingState,
        checkpointer=checkpointer or _SAVER,
        middleware=[TrimMiddleware(max_history_tokens or 6000), SlotsMiddleware()],
    )


def thread_id_of(current_user) -> str:
    """返回这个用户**当前会话**的 thread_id，形如 `user:10:s3`。

    前端不传 thread_id（L2-PLAN 决策 D2），所以会话边界由服务端推断：
    30 分钟没说话就开新会话，见 `app/ai/session.py`。

    ★ 为什么不再用 `f"user:{id}"`（2026-10-25 改）

      那样一个用户从注册到永远都在同一个 thread 里。实测：

          同一句话，带 110 个 checkpoint 的历史 → 17,508 input token
          干净 thread                            →  3,489 input token

      差 5 倍。上下文裁剪只能削峰（预算 6000），解决不了这个累积问题。

    代价：同一用户开两个标签页仍会共享上下文（会话按用户算，不是按页面）。
    """
    from app.ai.session import thread_id_for

    return thread_id_for(current_user.id)
