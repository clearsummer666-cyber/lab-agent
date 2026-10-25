"""评测的四个数据结构。所有模块都只依赖这里，互不依赖。

    Task        一道题（输入 + 初始条件 + 成功标准 + 禁止行为）
    RunResult   被测对象跑完一次留下的现场（相当于 trace 的摘要）
    CheckResult 一条断言的结论
    Grade       一道题的最终判定
"""

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    suite: str
    user_says: str = ""                           # 单轮用例
    turns: list[str] = field(default_factory=list)    # 多轮用例（优先）
    slots: dict = field(default_factory=dict)     # 这条用例对应的槽位（模拟模型已抽对）
    approve: bool = True                          # 模拟人工确认的结果
    expect: dict = field(default_factory=dict)    # 成功标准
    forbid: list[str] = field(default_factory=list)   # 禁止行为

    @property
    def all_turns(self) -> list[str]:
        """用户说的每一轮。多轮用 turns，单轮用 user_says。"""
        if self.turns:
            return self.turns
        return [self.user_says] if self.user_says else [""]

    @property
    def last_says(self) -> str:
        return self.all_turns[-1]


@dataclass
class RunResult:
    """一次试跑的现场。真项目里这就是 trace 的摘要版。"""

    task_id: str
    bookings_before: int
    bookings_after: int
    slots_after: dict
    intent: str | None
    tool_calls: list[str]
    reply: str
    approved: bool
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def delta(self) -> int:
        return self.bookings_after - self.bookings_before

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Grade:
    task_id: str
    suite: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    guardrail_hits: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    skipped: str = ""          # 非空表示「环境失败」，不计入通过率分母

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    @property
    def counted(self) -> bool:
        """这条用不用计入分母。环境失败的跳过。"""
        return not self.skipped


class EnvFailure(Exception):
    """环境故障：限流、超时、网络。

    ★ 单独一个异常类型，因为它不该算成 Agent 失败。
      限流算进分数，等于让 Agent 给基础设施背锅。
    """
