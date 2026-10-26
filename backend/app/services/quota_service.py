"""AI 用量限额：按**登录会话**统计，超限就拒。

口径说明（2026-10-26 改）：
    一开始是按天算，但测试时要等第二天才能重置，很不方便。
    改成按「本次登录」算 —— JWT 里加了 `iat`（签发时间），
    **重新登录 → 新 token → 新的 iat → 额度自动刷新**。
    所以一个账号反复登录就能反复拿到额度，这正是测试想要的。

为什么用 `agent_runs` 聚合，而不是单独维护一张计数表：
    `agent_runs` 本来就有 user_id / create_time / input_tokens / output_tokens，
    每次调用都会落一条（`agent_service` 里的旁路埋点），
    直接 COUNT / SUM 就是最准的账本 —— 再维护一张计数表反而要担心两边不一致，
    还得处理"对话失败要不要回滚计数"这种边界。

三层闸门（谁先撞线算谁）：
    1. 会话次数：本次登录后最多 N 轮对话（默认 10）
    2. 会话 token：本次登录后最多 N token（默认 25000，≈2 次完整预约）
    3. 日兜底：一天总量上限（默认 80 次 / 20 万 token，**登录不清零**）
       —— 只防「反复重登无限刷」，正常演示撞不到；设 0 可关闭。
管理员不限额（`role == "admin"` 直接放行）。

实测参考：一次完整预约 = 3 轮对话 ≈ 12,431 token（输入 11,974 / 输出 457）。
输入占大头是因为 system prompt + 9 个工具的 schema 每轮都要重发。
"""

from datetime import date, datetime
from datetime import time as dtime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import settings
from app.models.agent_run import AgentRun
from app.models.user import User


def _today_start() -> datetime:
    """今天 00:00:00 —— 日兜底从这里算起（服务器时区已设 Asia/Shanghai）"""
    return datetime.combine(date.today(), dtime.min)


def _sum_since(db: Session, user_id: int, since: datetime) -> tuple[int, int]:
    """从 since 到现在，该用户跑了多少轮、烧了多少 token"""
    runs, tokens = (
        db.query(
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0),
        )
        .filter(AgentRun.user_id == user_id, AgentRun.create_time >= since)
        .one()
    )
    return int(runs or 0), int(tokens or 0)


def get_usage(db: Session, user: User, since: datetime) -> dict:
    """会话用量 + 日兜底用量。限额为 0 表示不限；管理员恒不限。

    since：本次登录的签发时间（`get_token_issued_at` 给的，本地 naive datetime）
    """
    s_runs, s_tokens = _sum_since(db, user.id, since)
    d_runs, d_tokens = _sum_since(db, user.id, _today_start())

    is_admin = user.role == "admin"
    s_run_limit = 0 if is_admin else settings.AI_SESSION_RUN_LIMIT
    s_token_limit = 0 if is_admin else settings.AI_SESSION_TOKEN_LIMIT
    d_run_limit = 0 if is_admin else settings.AI_DAILY_RUN_LIMIT
    d_token_limit = 0 if is_admin else settings.AI_DAILY_TOKEN_LIMIT

    return {
        "is_admin": is_admin,
        "since": since.strftime("%Y-%m-%d %H:%M:%S"),
        # 会话维度（登录刷新）
        "run_used": s_runs,
        "run_limit": s_run_limit,
        "run_left": -1 if not s_run_limit else max(s_run_limit - s_runs, 0),
        "token_used": s_tokens,
        "token_limit": s_token_limit,
        "token_left": -1 if not s_token_limit else max(s_token_limit - s_tokens, 0),
        # 日兜底（登录不清零）
        "daily_run_used": d_runs,
        "daily_run_limit": d_run_limit,
        "daily_token_used": d_tokens,
        "daily_token_limit": d_token_limit,
        "max_input_chars": settings.AI_MAX_INPUT_CHARS,
    }


def check(db: Session, user: User, since: datetime) -> None:
    """对话前校验。超限抛 BusinessException（code=429，HTTP 仍是 200）。

    为什么 code=429 但 HTTP 200：
        项目的 business_exception_handler 统一返回 HTTP 200 + {code, message}，
        前端 axios / fetch 都按 body 里的 code 判断，这样不用改全局错误处理。
    """
    u = get_usage(db, user, since)

    # ① 会话次数
    if u["run_limit"] and u["run_used"] >= u["run_limit"]:
        raise BusinessException(
            message=(
                f"本次登录的 AI 对话次数已用完（{u['run_limit']} 次）。"
                f"退出后重新登录即可刷新额度。"
            ),
            code=429,
        )

    # ② 会话 token
    if u["token_limit"] and u["token_used"] >= u["token_limit"]:
        raise BusinessException(
            message=(
                f"本次登录的 AI 额度已用完（{u['token_limit']} token，约 2 次完整预约）。"
                f"退出后重新登录即可刷新额度。"
            ),
            code=429,
        )

    # ③ 日兜底（登录不清零）——只防反复重登无限刷
    if u["daily_run_limit"] and u["daily_run_used"] >= u["daily_run_limit"]:
        raise BusinessException(
            message=f"今日 AI 对话总量已达上限（{u['daily_run_limit']} 次），明天再来。",
            code=429,
        )
    if u["daily_token_limit"] and u["daily_token_used"] >= u["daily_token_limit"]:
        raise BusinessException(
            message=f"今日 AI token 总量已达上限（{u['daily_token_limit']}），明天再来。",
            code=429,
        )


def check_input_length(text: str) -> None:
    """单条消息长度上限 —— 防一次粘贴几千字把输入 token 顶爆。"""
    limit = settings.AI_MAX_INPUT_CHARS
    if limit and len(text) > limit:
        raise BusinessException(
            message=f"单条消息最多 {limit} 字，当前 {len(text)} 字，请精简后再发。",
            code=400,
        )
