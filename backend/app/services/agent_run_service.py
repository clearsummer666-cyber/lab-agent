"""Agent 调用日志服务。

**核心原则：旁路。** 这里的写操作失败，绝不能影响对话本身。
调用方（agent_service）在 finally 里调 record_run，本函数内部再兜一层 try/except，
两层保险 —— 日志是观察者，观察者挂掉不该让被观察者一起死。
"""

import logging
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.agent_run import AgentRun

logger = logging.getLogger(__name__)


def record_run(
    db: Session,
    *,
    user_id: int,
    thread_id: str,
    question: str,
    status: int,
    duration_ms: int,
    tool_calls: list[str],
    input_tokens: int,
    output_tokens: int,
    error: str | None = None,
    model_ms: int = 0,
    tool_ms: int = 0,
) -> None:
    """写一条调用记录。**永不抛异常**，失败只记 warning。"""
    try:
        run = AgentRun(
            user_id=user_id,
            thread_id=thread_id,
            question=(question or "")[:500],
            status=status,
            duration_ms=max(duration_ms, 0),
            model_ms=max(model_ms, 0),
            tool_ms=max(tool_ms, 0),
            tool_calls=",".join(tool_calls)[:1000],
            tool_count=len(tool_calls),
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            error=(error or "")[:255] or None,
        )
        db.add(run)
        db.commit()
    except Exception:  # noqa: BLE001
        # 注意这里必须 rollback：否则脏 session 会污染后续请求（比如同一个 db 还要查别的）
        db.rollback()
        logger.warning("[agent_run] 写调用日志失败，不影响对话", exc_info=True)


def get_run_page_list(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    status: int | None = None,
    user_id: int | None = None,
    keyword: str | None = None,
):
    """分页查询调用日志，按时间倒序。"""
    query = db.query(AgentRun)
    if status is not None:
        query = query.filter(AgentRun.status == status)
    if user_id is not None:
        query = query.filter(AgentRun.user_id == user_id)
    if keyword:
        query = query.filter(AgentRun.question.like(f"%{keyword}%"))

    total = query.count()
    rows = (
        query.order_by(AgentRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"list": rows, "total": total}


def get_run_detail(db: Session, run_id: int) -> AgentRun:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise BusinessException(message="调用记录不存在")
    return run


def get_stats(db: Session, days: int = 7) -> dict:
    """运营后台统计：四张卡 + 按天趋势 + 工具调用排行。

    为什么不在 SQL 里算工具排行：tool_calls 是逗号分隔的字符串，
    SQL 拆字符串既慢又不可移植。日志量级不大，取回来在 Python 里数更实在。
    """
    days = max(1, min(days, 90))
    start = datetime.now() - timedelta(days=days - 1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    base = db.query(AgentRun).filter(AgentRun.create_time >= start)

    total = base.count()
    failed = base.filter(AgentRun.status == 1).count()
    success = total - failed

    agg = db.query(
        func.coalesce(func.avg(AgentRun.duration_ms), 0),
        func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0),
        func.coalesce(func.avg(AgentRun.model_ms), 0),
        func.coalesce(func.avg(AgentRun.tool_ms), 0),
    ).filter(AgentRun.create_time >= start).one()
    avg_duration_ms = int(agg[0] or 0)
    total_tokens = int(agg[1] or 0)
    avg_model_ms = int(agg[2] or 0)
    avg_tool_ms = int(agg[3] or 0)

    # 按天聚合：SQL 只负责分组，补零在 Python 做（否则前端要处理缺失日期）
    day_col = func.date(AgentRun.create_time)
    rows = (
        db.query(
            day_col.label("d"),
            func.count(AgentRun.id),
            # 用 case 而不是 MySQL 的 IF()：换库（SQLite 测试库）也能跑
            func.sum(case((AgentRun.status == 1, 1), else_=0)),
            func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0),
        )
        .filter(AgentRun.create_time >= start)
        .group_by(day_col)
        .order_by(day_col)
        .all()
    )
    bucket = {
        str(r[0]): {"total": int(r[1] or 0), "failed": int(r[2] or 0), "tokens": int(r[3] or 0)}
        for r in rows
    }
    by_day = []
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        item = bucket.get(str(d), {"total": 0, "failed": 0, "tokens": 0})
        by_day.append({"date": d.strftime("%m-%d"), **item})

    # 工具排行：取回 tool_calls 在内存里数
    calls = [
        r[0]
        for r in db.query(AgentRun.tool_calls)
        .filter(AgentRun.create_time >= start, AgentRun.tool_calls != "")
        .all()
    ]
    counter = Counter()
    for text in calls:
        for name in str(text).split(","):
            name = name.strip()
            if name:
                counter[name] += 1
    top_tools = [{"name": n, "count": c} for n, c in counter.most_common(8)]

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "success_rate": round(success / total * 100, 1) if total else 0.0,
        "avg_duration_ms": avg_duration_ms,
        "avg_model_ms": avg_model_ms,
        "avg_tool_ms": avg_tool_ms,
        "total_tokens": total_tokens,
        "avg_tokens": int(total_tokens / total) if total else 0,
        "by_day": by_day,
        "top_tools": top_tools,
    }
