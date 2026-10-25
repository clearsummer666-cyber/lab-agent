"""harness：把任务发给被测对象、准备环境、记录现场、评分、汇总。

三件事：
    run_once  跑一遍整套题（每条题前重置环境）
    summarize 按 suite 汇总通过率
    run_k     同一套题跑 k 次，算 pass@k 和 pass^k

★ harness 只认"有 run(task) 方法"的对象，不关心它是假 Agent 还是真 Agent。
  下一课换真模型，这个文件一行都不用改。
"""

import copy

import data
from eval.core import Grade, RunResult, Task
from eval.graders import grade

INITIAL = copy.deepcopy(data.BOOKINGS)


def reset_db() -> None:
    """每条题开始前把库恢复原状。否则前一条题的副作用会污染后一条。"""
    data.BOOKINGS[:] = INITIAL


def run_once(tasks: list[Task], sut, on_result=None) -> list[Grade]:
    """跑一遍整套题。SUT 抛异常也要记成一条结果，不能让整个评测崩掉。

    ★ 区分两种异常：
      EnvFailure  环境故障（限流/超时）→ 标记 skipped，不计入分母
      其他异常    Agent 自己崩了      → 记成 fail，计入分母
    """
    from eval.core import EnvFailure

    grades: list[Grade] = []
    for t in tasks:
        reset_db()
        before = len(data.BOOKINGS)
        try:
            result = sut.run(t)
        except EnvFailure as exc:
            g = Grade(t.id, t.suite, False, skipped=str(exc))
            grades.append(g)
            if on_result:
                on_result(g)
            continue
        except Exception as exc:                      # noqa: BLE001
            result = RunResult(
                task_id=t.id, bookings_before=before, bookings_after=before,
                slots_after={}, intent=None, tool_calls=[], reply="",
                approved=t.approve, error=f"{type(exc).__name__}: {exc}",
            )
        g = grade(t, result)
        grades.append(g)
        if on_result:
            on_result(g)
    return grades


def summarize(grades: list[Grade]) -> list[dict]:
    """按 suite 分组汇总。跳过的用例不算进分母。"""
    buckets: dict[str, list[Grade]] = {}
    for g in grades:
        if not g.counted:
            continue
        buckets.setdefault(g.suite, []).append(g)

    rows = []
    for suite, items in buckets.items():
        ok = sum(1 for g in items if g.passed)
        rows.append({
            "suite": suite,
            "total": len(items),
            "passed": ok,
            "rate": ok / len(items),
            "guardrail": sum(len(g.guardrail_hits) for g in items),
            "tokens": sum(g.tokens_in + g.tokens_out for g in items),
        })
    return rows


def token_report(grades: list[Grade]) -> dict:
    """整轮评测的 token 账。开发期成本就靠这个盯。"""
    graded = [g for g in grades if g.counted]
    tin = sum(g.tokens_in for g in graded)
    tout = sum(g.tokens_out for g in graded)
    n = len(graded) or 1
    return {
        "in": tin,
        "out": tout,
        "total": tin + tout,
        "per_task": (tin + tout) / n,
        "tasks": len(graded),
    }


def run_k(tasks: list[Task], sut, k: int) -> dict:
    """同一套题跑 k 次。

    pass@k  k 次里至少成功一次  -> 能力边界（它到底会不会）
    pass^k  连续 k 次都成功      -> 稳定性（用户每次来都得办成）
    """
    runs = [run_once(tasks, sut) for _ in range(k)]

    per_task = []
    for i, t in enumerate(tasks):
        results = [runs[j][i].passed for j in range(k)]
        per_task.append({
            "id": t.id,
            "suite": t.suite,
            "pass@k": any(results),
            "pass^k": all(results),
            "hits": sum(results),
            "k": k,
        })

    n = len(tasks)
    return {
        "per_task": per_task,
        "pass@k": sum(x["pass@k"] for x in per_task) / n,
        "pass^k": sum(x["pass^k"] for x in per_task) / n,
        "avg": sum(x["hits"] for x in per_task) / (n * k),
        "k": k,
    }
