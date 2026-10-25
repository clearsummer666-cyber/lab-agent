"""记忆裁剪的算法层。

第 9 课里这些函数写在 demo 文件里，第 10 课要接进 middleware 复用，
所以抽出来单独放。真项目里这层会被 `lab-agent` 直接复用。

三个能力：
    find_pins    找出"不可驱逐"的消息下标
    pinned_trim  保护式裁剪
    check_pairing 校验工具调用配对（防孤儿 ToolMessage）
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

# ★ 中文字符/token 比。count_tokens_approximately 默认 4.0，是给英文的，
#   中文实测低估 2.27 倍（875 字估 223，真实 506）——
#   于是「预算 6000」实际放进去 13,600 真实 token，裁剪形同虚设。
#   1.7 实测偏差 +3%（521 vs 真实 506），见 2026-10-25 的对照实验。
CHARS_PER_TOKEN = 1.7


# ── 规则层：什么算"不可驱逐" ────────────────────────────
def is_pinned(m) -> bool:
    """逐条就能判断的规则：系统消息 / 用户说"记住"的。

    注意：判断不了"是不是第一条用户消息"——那要看位置，只能放在 find_pins()。
    """
    if isinstance(m, SystemMessage):
        return True
    if isinstance(m, HumanMessage) and "记住" in str(m.content):
        return True
    return False


def find_pins(messages) -> list[int]:
    """三条规则合起来：

    1. is_pinned() 逐条能判的（system / 含"记住"）
    2. 第一条 HumanMessage —— 任务目标（需要位置信息，只能在这层做）
    """
    pins = [i for i, m in enumerate(messages) if is_pinned(m)]
    first_user = next(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), None
    )
    if first_user is not None and first_user not in pins:
        pins.append(first_user)
    return sorted(pins)


# ── 校验层：工具调用配对 ────────────────────────────────
def check_pairing(messages) -> list[str]:
    """每个 tool 消息都必须能找到它的 assistant(tool_calls)。"""
    problems: list[str] = []
    open_ids: set[str] = set()
    for i, m in enumerate(messages):
        if isinstance(m, AIMessage) and m.tool_calls:
            open_ids = {t["id"] for t in m.tool_calls}
        elif isinstance(m, ToolMessage):
            if m.tool_call_id not in open_ids:
                problems.append(f"[{i}] 孤儿 ToolMessage")
            else:
                open_ids.discard(m.tool_call_id)
    return problems


# ── 修复层：把不完整的工具调用配对剔掉 ──────────────────
def sanitize_pairs(messages):
    """删掉不成对的工具调用。

    两种情况都要处理：
        1. assistant(tool_calls) 被保留，但它的 tool 结果没全留下 → 整组删掉
        2. tool 消息被保留，但下单的 assistant 没了 → 这条 tool 删掉

    第 9 课只处理了「开头连续 tool」，实测发现中间也会出现孤儿。
    """
    out = []
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]

        if isinstance(m, AIMessage) and m.tool_calls:
            need = {t["id"] for t in m.tool_calls}
            j = i + 1
            got: set[str] = set()
            while j < n and isinstance(messages[j], ToolMessage):
                got.add(messages[j].tool_call_id)
                j += 1
            if need <= got:              # 结果齐全 → 整组保留
                out.extend(messages[i:j])
            # 缺结果 → 整组跳过（assistant 和它的 tool 一起丢）
            i = j
            continue

        if isinstance(m, ToolMessage):    # 走到这里说明前面没有下单的 assistant
            i += 1
            continue

        out.append(m)
        i += 1
    return out


# ── 裁剪层 ─────────────────────────────────────────────
def pinned_trim(messages, max_tokens: int):
    """先保锚点，剩余预算从后往前填，最后按原下标拼回去。"""
    pins = set(find_pins(messages))
    used = count_tokens_approximately(
        [messages[i] for i in sorted(pins)], chars_per_token=CHARS_PER_TOKEN
    )
    budget = max_tokens - used

    tail: set[int] = set()
    for i in range(len(messages) - 1, -1, -1):
        if i in pins:
            continue
        cost = count_tokens_approximately(
            [messages[i]], chars_per_token=CHARS_PER_TOKEN
        )
        if cost > budget:
            break
        budget -= cost
        tail.add(i)

    keep = pins | tail
    # 按原顺序拼回，不能"锚点 + 尾部"直接接
    out = [m for i, m in enumerate(messages) if i in keep]

    # 修：剔掉不成对的工具调用（孤儿可能在任意位置，不只开头）
    out = sanitize_pairs(out)

    problems = check_pairing(out)
    if problems:
        raise ValueError(f"裁剪后历史不合法：{problems}")
    return out
