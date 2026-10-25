"""记忆的保留策略：多久不用就清掉。

★ 为什么需要：
  checkpointer 落 SQLite 之后，历史只增不减。
  实测跑了几轮测试就 212 行 checkpoints / 409 行 writes，
  而真实用户是 13 条预约的规模 —— 不清理迟早撑爆。

★ 为什么单独一个 sqlite 文件：
  LangGraph 的 checkpoints.sqlite 会被 checkpointer 频繁写，
  再往里加表容易撞锁。`data/threads.db` 只存一张"最后活跃时间"表，互不干扰。

★ 为什么不用 checkpoint_id 算时间：
  实测 `metadata` 里只有 source/step/parents，**没有时间戳**。
  UUID 里虽然可能编码了时间，但那是 LangGraph 内部格式，不该依赖。
  自己记 `last_seen` 最稳。
"""

import sqlite3
import time
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "threads.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 默认保留 7 天。预约是低频业务，7 天足够覆盖"上周约的这次来改"。
DEFAULT_TTL_DAYS = 7

# 自动清理的触发器：每 N 次对话检查一次，不用每次都扫表
_CHECK_EVERY = 100
_counter = 0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS thread_activity (
               thread_id TEXT PRIMARY KEY,
               last_seen REAL NOT NULL
           )"""
    )
    return conn


def touch(thread_id: str) -> None:
    """记一下这个 thread 刚活跃过。每次对话调一次。"""
    now = time.time()
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO thread_activity (thread_id, last_seen) VALUES (?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET last_seen = excluded.last_seen""",
            (thread_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def list_stale(days: int = DEFAULT_TTL_DAYS) -> list[str]:
    """列出超过 days 天没动过的 thread。"""
    cutoff = time.time() - days * 86400
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT thread_id FROM thread_activity WHERE last_seen < ?", (cutoff,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def purge(days: int = DEFAULT_TTL_DAYS, saver=None) -> dict:
    """清掉过期 thread 的对话历史。

    saver 传 checkpointer（要它的 delete_thread）。
    不传只清活跃时间表，不碰对话数据 —— 那是干跑，用来看看会删谁。
    """
    stale = list_stale(days)
    deleted, failed = [], []

    if saver is not None:
        for tid in stale:
            try:
                saver.delete_thread(tid)
                deleted.append(tid)
            except Exception:                       # noqa: BLE001
                failed.append(tid)

        conn = _connect()
        try:
            for tid in deleted:
                conn.execute("DELETE FROM thread_activity WHERE thread_id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()

    return {
        "stale": stale,
        "deleted": deleted,
        "failed": failed,
        "days": days,
    }


def maybe_purge(saver, days: int = DEFAULT_TTL_DAYS) -> dict | None:
    """每 _CHECK_EVERY 次调用才真正扫一次。返回 None 表示这次跳过了。"""
    global _counter
    _counter += 1
    if _counter % _CHECK_EVERY != 0:
        return None
    return purge(days=days, saver=saver)
