"""评测入口（离线黄金集）。

用法（在 backend/tests/ 下）：
    python run_eval.py                      # 假 Agent，零成本，秒出
    python run_eval.py --critical --k 5     # 关键路径 pass^5
    python run_eval.py --only C01,C03       # 只跑指定用例

判定标准：
    所有 expect 断言通过 且 没踩 forbid 里的护栏 = 通过

注意：本仓库只带**离线**评测（假 Agent + 假数据）。
端到端评测要真 MySQL + 真模型，脚本不随仓库提供，见 README「快速开始」。
"""

import argparse
import sys
from pathlib import Path

# run_eval.py 在 backend/tests/ 下，把该目录加进 sys.path
# 这样 `import data` 和 `from eval.harness import ...` 都能解析
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from eval.harness import run_k, run_once, summarize, token_report  # noqa: E402
from eval.tasks import CRITICAL, GOLDEN  # noqa: E402


def bar(rate: float, width: int = 18) -> str:
    filled = round(rate * width)
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sut", choices=["scripted", "real"], default="scripted")
    ap.add_argument("--k", type=int, default=1, help="跑几遍（>1 时算 pass@k / pass^k）")
    ap.add_argument("--critical", action="store_true", help="只跑关键路径用例")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--verbose", action="store_true", help="打印每条结果")
    args = ap.parse_args()

    tasks = GOLDEN
    if args.critical:
        tasks = [t for t in tasks if t.id in CRITICAL]
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        tasks = [t for t in tasks if t.id in want]
    if not tasks:
        print("没有匹配的用例")
        return 1

    if args.sut == "real":
        print("本仓库只带离线评测（假 Agent + 假数据，零成本）。")
        print("真模型端到端评测要真 MySQL + 真模型，脚本不随仓库提供，")
        print("见 README「四、快速开始 → 4.1 后端」末尾说明。")
        return 1

    from eval.sut import ScriptedAgent

    sut = ScriptedAgent()
    label = "假 Agent（零成本）"

    print(f"[评测] {label} · {len(tasks)} 道题 · 跑 {args.k} 遍\n")

    def show(g):
        """边跑边打，不用等全部跑完。"""
        if g.skipped:
            print(f"  [SKIP] {g.task_id}  {g.suite}  环境故障：{g.skipped}", flush=True)
        elif args.verbose or not g.passed:
            mark = "PASS" if g.passed else "FAIL"
            print(f"  [{mark}] {g.task_id}  {g.suite}", flush=True)
            for c in g.checks:
                print(f"         {'ok ' if c.ok else 'X  '} {c.name}：{c.detail}")
            for h in g.guardrail_hits:
                print(f"         !!  护栏触发：{h}")
        elif g.passed:
            print(f"  [PASS] {g.task_id}", flush=True)

    if args.k == 1:
        grades = run_once(tasks, sut, on_result=show)
        skipped = [g for g in grades if not g.counted]
        print("\n按 suite 汇总：")
        for row in summarize(grades):
            print(f"  {row['suite']:<14} {row['passed']}/{row['total']}  "
                  f"{bar(row['rate'])} {row['rate']:.0%}")

        graded = [g for g in grades if g.counted]
        ok = sum(1 for g in graded if g.passed)
        n = len(graded) or 1
        line = f"\n总计 {ok}/{len(graded)} = {ok / n:.0%}"
        if skipped:
            line += f"　（另有 {len(skipped)} 条环境故障跳过，不计入分母）"
        print(line)

        t = token_report(grades)
        if t["total"]:
            print(f"token 消耗：输入 {t['in']:,} / 输出 {t['out']:,} / "
                  f"合计 {t['total']:,}（平均每条 {t['per_task']:.0f}）")
        else:
            print("token 消耗：0（假 Agent 不调模型）")
        return 0 if ok == len(graded) else 1

    stat = run_k(tasks, sut, k=args.k)
    print(f"  {'题号':<6}{'suite':<14}{'命中':<8}{'pass@k':<9}{'pass^k'}")
    for x in stat["per_task"]:
        print(f"  {x['id']:<6}{x['suite']:<14}{x['hits']}/{x['k']:<7}"
              f"{'是' if x['pass@k'] else '否':<9}{'是' if x['pass^k'] else '否'}")

    print(f"\n  pass@{args.k}（至少成一次）   {stat['pass@k']:.0%}   {bar(stat['pass@k'])}")
    print(f"  pass^{args.k}（连续都成）     {stat['pass^k']:.0%}   {bar(stat['pass^k'])}")
    print(f"  单次平均              {stat['avg']:.0%}   {bar(stat['avg'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
