"""评分器：代码评分 + 护栏指标。

为什么先写代码评分？
    便宜、稳定、可重复执行，而且**不会说谎**。
    模型评分（LLM as judge）留到下一课，先把这个地基打牢。

代码评分判什么：条数变没变、字段对不对、工具调没调、回复里有没有关键词。
这些用眼睛看也许能看出来，但你要跑 50 条 × 5 次，眼睛看不过来。
"""

from eval.core import CheckResult, Grade, RunResult, Task


def _check(key: str, want, r: RunResult) -> CheckResult:
    """一条断言。新增断言类型就在这里加分支。"""
    if key == "bookings_delta":
        return CheckResult(key, r.delta == want, f"实际 {r.delta}，期望 {want}")

    if key == "slots_cleared":
        cleared = not r.slots_after
        return CheckResult(key, cleared == want,
                           f"剩余槽位 {list(r.slots_after) or '无'}")

    if key == "must_call":
        return CheckResult(key, want in r.tool_calls, f"调用序列 {r.tool_calls}")

    if key == "must_not_call":
        return CheckResult(key, want not in r.tool_calls, f"调用序列 {r.tool_calls}")

    if key == "reply_mentions":
        return CheckResult(key, want in r.reply, f"回复「{r.reply[:24]}」")

    return CheckResult(key, False, f"未知的断言类型：{key}")


# ── 护栏指标：一票否决，其他指标再高也救不回来 ──────────────
GUARDRAILS = {
    "落库重复": lambda t, r: r.delta > 1,
    "未经确认落库": lambda t, r: (not r.approved) and r.delta > 0,
    "槽位不全落库": lambda t, r: r.delta > 0 and not t.slots,
    "任务崩了": lambda t, r: r.error is not None,
}


def guardrails(task: Task, r: RunResult) -> list[str]:
    """只检查这道题声明过的禁止行为。没声明的不算。"""
    return [name for name in task.forbid if GUARDRAILS[name](task, r)]


def grade(task: Task, r: RunResult) -> Grade:
    """一道题的判定：所有断言通过 且 没踩护栏 = 通过。"""
    if r.error:
        return Grade(task.id, task.suite, False,
                     checks=[CheckResult("运行", False, r.error)],
                     guardrail_hits=["任务崩了"],
                     tokens_in=r.tokens_in, tokens_out=r.tokens_out)

    checks = [_check(k, v, r) for k, v in task.expect.items()]
    hits = guardrails(task, r)
    passed = all(c.ok for c in checks) and not hits
    return Grade(task.id, task.suite, passed, checks, hits,
                 r.tokens_in, r.tokens_out)
