"""会话维度：给 thread_id 加上「这是第几次会话」。

## 为什么需要

原来 `thread_id = f"user:{id}"` —— 一个用户从注册到永远，所有对话都在同一个 thread 里。

实测代价（2026-10-25）：

    同一句话「明天化学分析实验室几点有空？」
      带 110 个 checkpoint 的历史 → input 17,508 token
      干净 thread                 → input  3,489 token
    差 5 倍。

用户用一年，thread 里几千条历史，成本和延迟都会持续劣化。
**上下文裁剪只能削峰**（预算 6000），解决不了「永远在同一个 thread」——
裁剪后的历史仍然远多于新会话。

## 做法：滑动窗口

同一个用户：

- 30 分钟内连续说话 → 同一个 thread（上下文接上，符合直觉）
- 超过 30 分钟没说话 → 新开一个 thread（历史清零）

**为什么不用「按天切」**：用户晚上 23:59 说到一半、00:01 继续，按天切会突然失忆。
滑动窗口按「离开多久」判断，更接近人对「一次对话」的感知。

**为什么不用前端传 session_id**：L2-PLAN 决策 D2 定了前端一行不改，
会话边界只能服务端推断。

## 代价（必须认）

改完之后，**旧 thread（`user:1` 这种）不再被读到**，相当于所有用户都重新开始。
这是必要的一次性代价，不是 bug。
"""

import sqlite3
import time

from app.ai.retention import _DB_PATH

# 30 分钟没说话就算新会话。选这个值的理由：
#   比一次预约流程（通常 2-5 分钟）长得多，不会中途断开；
#   又比「用户下次想起来用」的间隔短，避免把上周的对话接着聊。
DEFAULT_IDLE_MINUTES = 30


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_session (
               user_id     INTEGER PRIMARY KEY,
               session_no  INTEGER NOT NULL DEFAULT 0,
               last_active REAL    NOT NULL
           )"""
    )
    return conn


def thread_id_for(user_id: int, idle_minutes: int = DEFAULT_IDLE_MINUTES) -> str:
    """按「会话」返回 thread_id，形如 `user:10:s3`。

    同一个 user 在 idle_minutes 内多次调用，返回同一个 id（幂等）——
    一个请求里 `_config()` 和 `touch()` 都会调它，不能每次都递增。
    """
    now = time.time()
    conn = _connect()
    try:
        # BEGIN IMMEDIATE：先抢写锁。两个并发请求同时进来时，
        # 不加锁会读到同一个 session_no 再各自 +1，导致跳号。
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT session_no, last_active FROM user_session WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            session_no = 0
            conn.execute(
                "INSERT INTO user_session (user_id, session_no, last_active)"
                " VALUES (?, ?, ?)",
                (user_id, session_no, now),
            )
        elif now - row[1] > idle_minutes * 60:
            session_no = row[0] + 1
            conn.execute(
                "UPDATE user_session SET session_no = ?, last_active = ?"
                " WHERE user_id = ?",
                (session_no, now, user_id),
            )
        else:
            session_no = row[0]
            conn.execute(
                "UPDATE user_session SET last_active = ? WHERE user_id = ?",
                (now, user_id),
            )
        conn.commit()
    finally:
        conn.close()

    return f"user:{user_id}:s{session_no}"


def reset_session(user_id: int) -> str:
    """强制开一个新会话。评测用——保证每次跑的起点一致。"""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT session_no FROM user_session WHERE user_id = ?", (user_id,)
        ).fetchone()
        session_no = (row[0] if row else 0) + 1
        conn.execute(
            "INSERT INTO user_session (user_id, session_no, last_active)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET session_no = excluded.session_no,"
            " last_active = excluded.last_active",
            (user_id, session_no, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return f"user:{user_id}:s{session_no}"


def current_session(user_id: int) -> tuple[int, float] | None:
    """查当前会话编号和最后活跃时间。排查用。"""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT session_no, last_active FROM user_session WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
