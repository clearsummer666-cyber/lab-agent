"""预约接口并发压测脚本（纯标准库）—— 复现「压测 1.27s / 120 次」口径的工具。

定位：analyze_reservations 看的是历史数据，这个脚本造的是并发压力，
对应简历上「悲观锁 + 压测」那条可讲的故事。

**口径怎么来的**：
    8 线程 × 15 轮 = 120 个 POST /api/reservation 打向同一实验室同一时段。
    service 层先对实验室行 SELECT ... FOR UPDATE，把「查冲突 → 插入」
    串成临界区，120 个请求在行锁上排队：恰好 1 个 code=200，
    其余 119 个出锁后被判「该时段已预约」业务拒绝，全程 ≈1.27s。

**两种模式**：
    same-slot       全部请求抢同一日期（今天+30）同一时段
                    → 预期恰好 1 条成功，其余业务拒绝，量化悲观锁排队延迟
    distinct-dates  第 i 个线程的第 r 轮用 今天+30+i*rounds+r 天
                    → 每个请求日期唯一、互不冲突，预期全部成功，测无冲突并行吞吐
                    （rounds=1 时退化为「线程 i 用今天+30+i」的原始口径）

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/bench_reservation.py --mode same-slot
    ./.venv/Scripts/python.exe scripts/bench_reservation.py --mode distinct-dates
    ./.venv/Scripts/python.exe scripts/bench_reservation.py --threads 16 --rounds 10 --lab 3

注意（幂等）：脚本只写不删。重复跑会撞自己上次的成功单
（same-slot 全部变「该时段已预约」，成功数 0；distinct-dates 同理）。
清理交给人工：到管理后台驳回/取消旧单，或换 --lab / --start / --end 避开。

依赖：无第三方包。仅标准库 urllib.request / threading / argparse / csv /
statistics / json / time / datetime（外加 pathlib 拼路径，与
analyze_reservations.py 同款用法）。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# 单请求超时（秒）：要盖得住 120 个请求在同一把行锁上排队的最坏等待
REQUEST_TIMEOUT = 10.0

CSV_HEADER = ["ts", "thread", "round", "http_status", "biz_code", "latency_ms", "message"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="预约接口并发压测（纯标准库，复现「压测 1.27s / 120 次」口径）"
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--user", default="aaa", help="登录用户名")
    parser.add_argument("--password", default="123456", help="登录密码")
    parser.add_argument("--mode", choices=["same-slot", "distinct-dates"], default="same-slot",
                        help="same-slot=全部抢同一时段（预期恰好 1 成功）；"
                             "distinct-dates=各抢各的日期（预期全部成功）")
    parser.add_argument("--threads", type=int, default=8, help="并发线程数（每线程各自登录一次）")
    parser.add_argument("--rounds", type=int, default=15, help="每线程请求轮数")
    parser.add_argument("--lab", type=int, default=0, help="实验室 ID；0=自动取第一个开放实验室")
    parser.add_argument("--start", default="10:00", help="预约开始时间 HH:MM")
    parser.add_argument("--end", default="12:00", help="预约结束时间 HH:MM")
    parser.add_argument("--out", default="results", help="CSV 输出目录（相对 backend/scripts/）")
    args = parser.parse_args()
    if args.threads < 1 or args.rounds < 1:
        parser.error("--threads 和 --rounds 都必须 ≥ 1")
    return args


def _parse_json(raw: bytes) -> dict | None:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _text_of(raw: bytes) -> str:
    """从响应体里抠一句人话：统一格式取 message，FastAPI 校验错误取 detail。"""
    body = _parse_json(raw)
    if body:
        return str(body.get("message") or body.get("detail") or "")[:200]
    return raw[:200].decode("utf-8", "replace").strip()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def http_json(method: str, url: str, payload: dict | None = None,
              token: str | None = None) -> tuple[int, dict | None, str, float]:
    """发一个 JSON 请求，返回 (http_status, body, 错误说明, 耗时ms)。

    传输层异常在这里折算成 http_status=0 的结果返回、绝不往上层抛 ——
    压测线程里任何一条请求挂掉都不能带崩整轮。
    HTTPError / URLError 从 urllib.request 命名空间取，不用多引 urllib.error。
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.request.HTTPError as exc:  # 4xx/5xx：拿到了响应，不算传输失败
        raw = exc.read()
        return exc.code, _parse_json(raw), f"HTTP {exc.code}: {_text_of(raw)}", _elapsed_ms(started)
    except OSError as exc:  # URLError/超时/连接被拒（HTTPError 也是 OSError 子类，必须先接）
        return 0, None, f"连接失败: {exc}", _elapsed_ms(started)

    return status, _parse_json(raw), "", _elapsed_ms(started)


def login_once(base: str, user: str, password: str) -> tuple[str | None, int, int | None, float, str]:
    """登录一次拿 data.token。失败时 token=None，由调用方记一行 http_error。"""
    status, body, err, latency = http_json(
        "POST", f"{base}/api/auth/login", {"username": user, "password": password}
    )
    biz_code = body.get("code") if isinstance(body, dict) else None
    token = None
    message = err
    if status == 200 and biz_code == 200:
        token = (body.get("data") or {}).get("token")
        if not token:
            message = "登录响应缺 data.token"
    elif not message:
        message = str(body.get("message") or "") if isinstance(body, dict) else "响应不可解析"
    return token, status, biz_code, latency, message


def pick_open_lab(base: str, token: str) -> int:
    """--lab 0 时自动取第一个开放实验室：GET /api/lab/list?status=1 → data.list[0].id。"""
    status, body, err, _ = http_json(
        "GET", f"{base}/api/lab/list?page=1&page_size=200&status=1", token=token
    )
    if status != 200 or not isinstance(body, dict) or body.get("code") != 200:
        raise SystemExit(f"自动发现实验室失败：{err or (body or {}).get('message')}")
    items = (body.get("data") or {}).get("list") or []
    for item in items:
        if item.get("status") == 1:
            return int(item["id"])
    raise SystemExit("没有开放（status=1）的实验室：先在管理端开放一个，或用 --lab 直接指定")


def build_dates(mode: str, threads: int, rounds: int) -> list[list[str]]:
    """给每个 (thread, round) 预生成预约日期。

    same-slot：全部今天+30 同一天 —— 同一把悲观锁上的完全竞争。
    distinct-dates：今天+30+i*rounds+r —— 每个请求日期唯一（rounds=1 时
    就是「线程 i 用今天+30+i」），保证任何轮数下「全部成功」的口径成立。
    """
    base = date.today() + timedelta(days=30)
    if mode == "same-slot":
        return [[base.isoformat()] * rounds for _ in range(threads)]
    return [
        [(base + timedelta(days=i * rounds + r)).isoformat() for r in range(rounds)]
        for i in range(threads)
    ]


def now_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _record(rows: list, lock: threading.Lock, row: list) -> None:
    with lock:
        rows.append(row)


def worker(tid: int, base: str, args: argparse.Namespace, plan: list[list[str]],
           gate: threading.Event, round_gate: threading.Barrier,
           rows: list, lock: threading.Lock) -> None:
    """一个压测线程：各自登录一次 → 等起跑哨 → 每轮 barrier 对齐后打一发预约。"""
    token, status, biz_code, latency, message = login_once(base, args.user, args.password)
    if token is None:
        # 登录失败是环境问题（服务没起/密码错），记一行然后放掉 barrier，别让同伴干等
        _record(rows, lock, [now_ts(), tid, 0, status, biz_code, round(latency, 1),
                             f"LOGIN FAILED: {message}"])
        round_gate.abort()
        return

    gate.wait()
    try:
        for r in range(args.rounds):
            round_gate.wait(timeout=120)
            payload = {
                "lab_id": args.lab,
                "date": plan[tid][r],
                "start_time": args.start,
                "end_time": args.end,
            }
            status, body, err, latency = http_json(
                "POST", f"{base}/api/reservation", payload, token=token
            )
            biz_code = body.get("code") if isinstance(body, dict) else None
            if status != 200:
                message = err
            elif biz_code is None:
                message = "响应不是统一的 {code,message,data} 格式"
            else:
                message = str(body.get("message") or "")
            _record(rows, lock, [now_ts(), tid, r + 1, status, biz_code,
                                 round(latency, 1), message])
    except threading.BrokenBarrierError:
        return  # 有线程提前退场，本轮起剩下的轮次作废


def percentile(sorted_values: list[float], q: float) -> float:
    """线性插值取分位数。不用 statistics.quantiles：样本 <2 时它会抛异常。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def summarize(rows: list[list], wall_seconds: float) -> None:
    """控制台汇总：三桶计数 + 延迟分位 + 拒绝明细 + 总墙钟。"""
    total = len(rows)
    success = sum(1 for r in rows if r[3] == 200 and r[4] == 200)
    biz_reject = sum(1 for r in rows if r[3] == 200 and r[4] is not None and r[4] != 200)
    http_error = total - success - biz_reject  # 传输失败/非200/非JSON，原因都写在 message 里

    latencies = sorted(float(r[5]) for r in rows)

    line = "=" * 52
    print(line)
    throughput = total / wall_seconds if wall_seconds > 0 else 0.0
    print(f"压测汇总 · {total} 请求 · 墙钟 {wall_seconds:.2f}s · 吞吐 {throughput:.1f} req/s")
    print(line)
    print(f"  成功          {success:>4}")
    print(f"  业务拒绝      {biz_reject:>4}")
    print(f"  HTTP/连接错误 {http_error:>4}")
    if latencies:
        print(f"  延迟 ms  avg={statistics.fmean(latencies):.1f}"
              f"  p50={percentile(latencies, 0.50):.1f}"
              f"  p95={percentile(latencies, 0.95):.1f}"
              f"  max={latencies[-1]:.1f}")

    rejects: dict[str, int] = {}
    for r in rows:
        if r[3] == 200 and r[4] is not None and r[4] != 200:
            key = str(r[6]) or "(空 message)"
            rejects[key] = rejects.get(key, 0) + 1
    for key, cnt in sorted(rejects.items(), key=lambda kv: -kv[1]):
        print(f"  拒绝明细：{key} × {cnt}")
    print()


def write_csv(out_dir: Path, mode: str, rows: list[list]) -> Path:
    """明细落盘 results/bench_{mode}_{ts}.csv，utf-8-sig 方便 Excel 直接开。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"bench_{mode}_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    return path


def main() -> None:
    args = parse_args()
    base = args.base.rstrip("/")

    if args.lab == 0:  # 自动发现：主线程先登一次，只为拿 token 查实验室列表
        token, *_ = login_once(base, args.user, args.password)
        if token is None:
            raise SystemExit("自动发现实验室前的登录失败：检查 --user/--password，或后端是否已启动")
        args.lab = pick_open_lab(base, token)

    plan = build_dates(args.mode, args.threads, args.rounds)
    dates = sorted({d for row in plan for d in row})
    total = args.threads * args.rounds

    print(f"目标 {base}  模式 {args.mode}")
    print(f"实验室 {args.lab}  时段 {args.start}~{args.end}"
          f"  线程 {args.threads} × 轮数 {args.rounds} = {total} 请求")
    print(f"日期 {dates[0]}~{dates[-1]}" + ("" if len(dates) == 1 else f"（共 {len(dates)} 天）"))
    print("幂等提醒：脚本只写不删。重复跑会撞自己上次的成功单（全部变业务拒绝）。")
    print("清理交人工：管理后台驳回/取消旧单，或换 --lab / --start / --end 避开。")
    print()

    rows: list[list] = []
    lock = threading.Lock()
    gate = threading.Event()                      # 所有线程登录完成后同时起跑
    round_gate = threading.Barrier(args.threads)  # 每轮再对齐一次，保证每轮都是满并发
    pool = [
        threading.Thread(
            target=worker,
            args=(tid, base, args, plan, gate, round_gate, rows, lock),
            name=f"bench-{tid}",
            daemon=True,
        )
        for tid in range(args.threads)
    ]
    for t in pool:
        t.start()
    wall_start = time.perf_counter()
    gate.set()
    for t in pool:
        t.join()
    wall_seconds = time.perf_counter() - wall_start

    summarize(rows, wall_seconds)
    out_path = write_csv(Path(__file__).resolve().parent / args.out, args.mode, rows)
    print(f"明细已写入：{out_path}（共 {len(rows)} 行）")


if __name__ == "__main__":
    main()
