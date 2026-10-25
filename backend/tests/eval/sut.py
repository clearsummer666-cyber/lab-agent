"""被测对象（SUT = System Under Test）。

两个实现，接口完全一样（都有 `run(task) -> RunResult`），
所以 `harness.py` 一行都不用改就能换着跑：

    ScriptedAgent  规则版假 Agent。不调模型，秒出。用来验证评测框架本身。
    RealAgent      真模型 + 真工具。跑出来的才是真实数字。

★ 为什么要两个：
  框架写错了，测出来的分数全是假的，而你会照着假分数去改产品——
  这比 Agent 本身出错更麻烦。所以先用假 Agent 把尺子校准。
"""

import copy
import random
import time

from langchain_core.messages import AIMessage
from langgraph.types import Command

import data
from eval.core import EnvFailure, RunResult


def write_booking(lab_id: int, slots: dict) -> None:
    new_id = max((b["id"] for b in data.BOOKINGS), default=0) + 1
    data.BOOKINGS.append({
        "id": new_id,
        "lab_id": lab_id,
        "date": slots["book_date"],
        "start": slots["start"],
        "end": slots["end"],
        "people": slots["people"],
        "user": "我",
    })


class ScriptedAgent:
    """规则版预约 Agent。

    fail_rate    模拟真实 Agent 的概率性失败（这次没办成）
    skip_confirm 模拟一个违规实现：不问就落库
    keep_slots   模拟一个 bug：提交成功后忘了清槽位
    """

    def __init__(self, fail_rate: float = 0.0, skip_confirm: bool = False,
                 keep_slots: bool = False):
        self.fail_rate = fail_rate
        self.skip_confirm = skip_confirm
        self.keep_slots = keep_slots

    def run(self, task) -> RunResult:
        before = len(data.BOOKINGS)
        calls: list[str] = []
        slots = copy.deepcopy(task.slots)
        says = task.last_says

        # ── 概率性失败：模拟"这一轮没办成" ──
        if self.fail_rate and random.random() < self.fail_rate:
            return self._snap(task, before, calls, "闲聊", "我再确认一下信息，稍等", {})

        # ── 没有槽位：不该提交，只回答 ──
        if not slots:
            if "设备" in says:
                calls.append("list_lab_equipments")
                intent, reply = "咨询", "化学实验室有电子显微镜、离心机"
            elif "空" in says or "几点" in says or "情况" in says:
                calls.append("list_availability")
                intent, reply = "咨询", "明天化学实验室空闲 12:00-18:00"
            elif "约个" in says or "哪" in says:
                intent, reply = "咨询", "您想约哪个实验室、哪天几点？"
            elif "那间" in says or "那个" in says:
                intent, reply = "咨询", "您说的是化学实验室，明天还有空位"
            elif "不约" in says:
                intent, reply = "闲聊", "好的，已取消"
            else:
                intent, reply = "闲聊", "请问需要我帮您做什么？"
            return self._snap(task, before, calls, intent, reply, slots)

        # ── 有槽位：走预约 ──
        intent = "预约"

        # 用户还在改口 / 明确说先别提交：不该落库，等他确认
        if any(w in says for w in ("换成", "等等", "先别", "再想想")):
            calls.append("interrupt")
            return self._snap(task, before, calls, intent,
                              "好的，已按你说的更新，要现在提交吗？", slots)

        rows = data.find_labs(keyword=slots["lab_name"], only_open=False)
        if not rows:
            return self._snap(task, before, calls, intent,
                              f"没有叫「{slots['lab_name']}」的实验室", slots)
        lab = rows[0]
        lab_id = lab["id"]

        # 容量校验
        if slots["people"] > lab["capacity"]:
            return self._snap(task, before, calls, intent,
                              f"{lab['name']}最多容纳 {lab['capacity']} 人，"
                              f"你说的 {slots['people']} 人超出容量", slots)

        calls.append("check_available")
        if not data.is_available(lab_id, slots["book_date"], slots["start"], slots["end"]):
            reply = f"{slots['book_date']} {slots['start']}-{slots['end']} 已被占用"
            return self._snap(task, before, calls, intent, reply, slots)

        # 违规实现：跳过人工确认直接落库
        if self.skip_confirm:
            calls.append("create_lab_reservation")
            write_booking(lab_id, slots)
            return self._snap(task, before, calls, intent, "预约成功", {})

        calls.append("interrupt")
        if task.approve:
            calls.append("create_lab_reservation")
            write_booking(lab_id, slots)
            kept = slots if self.keep_slots else {}
            return self._snap(task, before, calls, intent, "预约成功，单号已生成", kept)

        return self._snap(task, before, calls, intent, "好的，没有提交", slots)

    @staticmethod
    def _snap(task, before, calls, intent, reply, slots) -> RunResult:
        return RunResult(
            task_id=task.id,
            bookings_before=before,
            bookings_after=len(data.BOOKINGS),
            slots_after=slots,
            intent=intent,
            tool_calls=list(calls),
            reply=reply,
            approved=task.approve,
        )


class RealAgent:
    """真模型 + 真工具。评测里跑的就是这个。

    复用 `agent/` 包里那套实现，和 lesson 里的演示是同一份代码。

    ★ 限流处理（2026-10-24 踩出来的）：
      免费 key 有速率限制，30 条连续跑会大面积 429。
      如果不处理，评测出来的分数是「限流的分数」，不是「Agent 的分数」。
      所以这里做两件事：
        1. 每次调用之间留间隔，主动降低速率
        2. 撞到 429 就指数退避重试
      环境故障不能算成 Agent 失败。
    """

    MAX_RETRY = 3

    def __init__(self, gap: float = 3.0, debug: bool = False):
        from agent import build_agent

        self._build = build_agent
        self.agent = build_agent()
        self._seq = 0
        self.gap = gap
        self.debug = debug

    def run(self, task) -> RunResult:
        before = len(data.BOOKINGS)
        self._seq += 1
        # ★ 每条用例一个全新的 Agent 实例：幂等表和 checkpointer 都是干净的。
        #   不这样做，前一条用例提交过的时段会让后一条「幂等命中」而假通过。
        self.agent = self._build()
        cfg = {"configurable": {"thread_id": f"{task.id}-{self._seq}"}}

        out = None
        for turn in task.all_turns:
            out = self._invoke({"messages": [{"role": "user", "content": turn}]}, cfg)
            out = self._drain(out, task, cfg)

        calls: list[str] = []
        tokens_in = tokens_out = 0
        for m in out["messages"]:
            if isinstance(m, AIMessage) and m.tool_calls:
                calls.extend(t["name"] for t in m.tool_calls)
            um = getattr(m, "usage_metadata", None) or {}
            tokens_in += um.get("input_tokens", 0) or 0
            tokens_out += um.get("output_tokens", 0) or 0

        from llm_utils import text_of

        return RunResult(
            task_id=task.id,
            bookings_before=before,
            bookings_after=len(data.BOOKINGS),
            slots_after=self.agent.get_state(cfg).values.get("slots") or {},
            intent=None,                      # 真 Agent 没有显式 intent 字段
            tool_calls=calls,
            reply=text_of(out["messages"][-1]),
            approved=task.approve,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def _invoke(self, payload, cfg):
        """带限流退避的调用。429 是环境问题，不该算 Agent 失败。"""
        for attempt in range(self.MAX_RETRY):
            t0 = time.time()
            try:
                out = self.agent.invoke(payload, cfg)
                if self.debug:
                    print(f"      [调用] {time.time() - t0:.1f}s", flush=True)
                time.sleep(self.gap)          # 主动降速，别把配额一次打光
                return out
            except Exception as exc:          # noqa: BLE001
                msg = str(exc)
                is_rate = "429" in msg or "RateLimit" in msg or "速率限制" in msg
                if not is_rate:
                    raise                     # 不是限流，原样抛出
                if attempt == self.MAX_RETRY - 1:
                    # ★ 重试耗尽 → 环境故障，不该算成 Agent 失败
                    raise EnvFailure(f"限流重试 {self.MAX_RETRY} 次仍失败") from exc
                wait = 5 * (2 ** attempt)     # 5, 10 秒
                print(f"      [限流] 等 {wait}s 后重试（第 {attempt + 1} 次）")
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def _drain(self, out, task, cfg, max_rounds: int = 3):
        """把挂起的确认都按 task.approve 答复掉。

        ★ max_rounds 是保险：如果模型反复触发 interrupt，这里会死循环。
          超过上限就停，让评测记成一条失败，而不是把整轮评测挂住。
        """
        rounds = 0
        while out.get("__interrupt__"):
            if rounds >= max_rounds:
                raise RuntimeError(
                    f"连续 {max_rounds} 次 interrupt 仍未结束，疑似死循环"
                )
            rounds += 1
            out = self._invoke(Command(resume={"approved": task.approve}), cfg)
        return out
