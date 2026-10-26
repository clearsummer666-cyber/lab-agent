"""预约数据分析脚本（pandas）—— 内部运营分析工具。

定位：它读的是业务库，产出的结论是给运营看的（哪个实验室最抢手、哪个时段最挤），
所以放在项目里是合理的，不属于「为了写简历硬塞」的东西。

**pandas 与 SQL 的分工（面试会问）**：
    - 库内聚合（COUNT / GROUP BY / JOIN）→ 交给 SQL，快、省内存
    - 取出来之后的二次加工（时间拆解、透视、导出、画图）→ 交给 pandas
    这个脚本故意两步都做，就是为了能讲清边界在哪。

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/analyze_reservations.py
    ./.venv/Scripts/python.exe scripts/analyze_reservations.py --days 30 --export

依赖：pandas（见 scripts/requirements-scripts.txt）
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

STATUS_MAP = {0: "待审核", 1: "已通过", 2: "已驳回", 3: "已取消"}
WEEKDAY_MAP = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

# 用一条 SQL 把需要的字段一次取全（避免 N+1：不要在循环里查库）
SQL = text(
    """
    SELECT r.id, r.lab_id, l.name AS lab_name, r.equipment_id,
           r.date, r.start_time, r.end_time, r.status
    FROM reservations r
    LEFT JOIN labs l ON l.id = r.lab_id
    WHERE r.date >= :start_date
    """
)


def load_frame(days: int) -> pd.DataFrame:
    """把数据取进 DataFrame。日期在库里是字符串，所以过滤也按字符串比。"""
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with engine.connect() as conn:
        df = pd.read_sql(SQL, conn, params={"start_date": start_date})
    if df.empty:
        return df

    # 类型转换：库里 start_time 是 "09:00" 字符串，转成真正的 time 才好算小时
    df["start_dt"] = pd.to_datetime(df["date"] + " " + df["start_time"], errors="coerce")
    df["end_dt"] = pd.to_datetime(df["date"] + " " + df["end_time"], errors="coerce")
    df["hours"] = (df["end_dt"] - df["start_dt"]).dt.total_seconds() / 3600
    # pandas 3.x 起 dt.dayofweek 已弃用（改名 day_of_week），
    # 这里用 datetime 自带的 weekday() 绕开，2.x / 3.x 都能跑且没有告警
    df["weekday"] = df["start_dt"].map(lambda d: d.weekday() if pd.notna(d) else None)
    df["weekday_cn"] = df["weekday"].map(WEEKDAY_MAP)
    df["status_cn"] = df["status"].map(STATUS_MAP).fillna("未知")
    df["slot"] = df["start_time"].str.slice(0, 2) + ":00"
    return df


def report(df: pd.DataFrame, days: int) -> None:
    line = "=" * 52
    print(line)
    print(f"预约数据分析 · 最近 {days} 天 · 共 {len(df)} 条")
    print(line)

    # ① 总量与状态分布（value_counts 等价于 SQL 的 GROUP BY + COUNT）
    print("\n【1】状态分布")
    status = df["status_cn"].value_counts()
    for name, cnt in status.items():
        pct = cnt / len(df) * 100
        print(f"  {name:<5}{cnt:>4} 条  {pct:>5.1f}%")

    # ② 各实验室热度：只在「有效预约」里算（排除驳回/取消），否则数据没意义
    valid = df[df["status"].isin([0, 1])]
    print("\n【2】实验室热度（仅统计待审核 + 已通过）")
    if valid.empty:
        print("  无有效预约")
    else:
        hot = valid.groupby("lab_name").agg(
            预约数=("id", "count"),
            平均时长小时=("hours", "mean"),
        ).sort_values("预约数", ascending=False)
        hot["平均时长小时"] = hot["平均时长小时"].round(1)
        print(hot.to_string())

    # ③ 时段分布：看哪个开始时段最挤
    print("\n【3】开始时段分布（仅有效预约）")
    if not valid.empty:
        slot = valid["slot"].value_counts().sort_index()
        peak = slot.idxmax()
        for s, cnt in slot.items():
            bar = "█" * cnt
            print(f"  {s}  {cnt:>3} 条  {bar}")
        print(f"  → 最挤时段：{peak}")

    # ④ 星期分布：pivot_table 就是 pandas 的透视表
    print("\n【4】星期 × 状态 透视")
    if not df.empty:
        pivot = pd.pivot_table(
            df, index="weekday_cn", columns="status_cn", values="id",
            aggfunc="count", fill_value=0,
        )
        print(pivot.to_string())

    # ⑤ 一句话结论
    print("\n【5】结论")
    if not valid.empty:
        top_lab = valid["lab_name"].value_counts().idxmax()
        print(f"  · 最热门实验室：{top_lab}")
        print(f"  · 最挤开始时段：{valid['slot'].value_counts().idxmax()}")
        print(f"  · 有效预约占比：{len(valid) / len(df) * 100:.1f}%")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="预约数据分析")
    parser.add_argument("--days", type=int, default=90, help="统计最近 N 天")
    parser.add_argument("--export", action="store_true", help="同时导出明细 CSV")
    args = parser.parse_args()

    df = load_frame(args.days)
    if df.empty:
        print(f"最近 {args.days} 天没有预约数据")
        return

    report(df, args.days)

    if args.export:
        out = Path(__file__).resolve().parent.parent / "data" / "spider" / "reservations_analysis.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = ["id", "lab_name", "date", "start_time", "end_time", "hours", "weekday_cn", "status_cn"]
        df[cols].to_csv(out, index=False, encoding="utf-8-sig")
        print(f"明细已导出：{out}")


if __name__ == "__main__":
    main()
