"""按意图选工具，砍掉用不上的 schema。

★ 为什么要做：
  实测第 1 轮输入就有 1206 token，其中 9 个工具的 schema 占了大部分。
  问一句「实验室的预约规则是什么」，根本用不到 create_lab_reservation
  那 376 token 的 schema，但每轮都在发。

★ 设计原则：**宁可多挂，不可少挂。**
  判断错方向的代价不对称：
    多挂 → 浪费几百 token，功能正常
    少挂 → 模型调不到工具，任务直接失败
  所以只在**明确是纯问答**时才裁剪，其余一律挂全集。

★ 判据（两个条件都满足才裁）：
  1. 命中问答类关键词：规则 / 开放时间 / 安全 / 借用 / 须知 / 流程
  2. **不含**任何动作类关键词：约 / 预约 / 取消 / 我的 / 订 / 排

  第 2 条是关键：用户说「预约规则是什么」虽然含「预约」，
  但它是问规则不是要下单 —— 所以这里用「预约」不算动作词，
  真正算动作的是「帮我约」「我要约」「取消」「我的预约」这类。
"""

import re

# ── 工具分组 ──────────────────────────────────────────
ASK = ["search_lab_docs", "list_open_labs", "list_lab_equipments"]
BOOK = [
    "list_open_labs", "list_availability", "get_today",
    "save_slot", "create_lab_reservation",
]
MANAGE = ["list_my_reservations", "cancel_my_reservation", "get_today"]

ALL_TOOL_NAMES = [
    "search_lab_docs", "list_open_labs", "list_lab_equipments", "list_availability",
    "get_today", "create_lab_reservation", "list_my_reservations",
    "cancel_my_reservation", "save_slot",
]

# 问知识、不产生业务动作
_ASK_PATTERN = re.compile(
    r"规则|开放时间|开放吗|开吗|几点开|几点关|什么时候开|"
    r"周末|工作日|安全|规范|须知|借用|怎么用|流程|要求|"
    r"能不能进|有什么设备|有哪些设备"
)

# 要产生业务动作（下单 / 查自己的单 / 取消）
_ACTION_PATTERN = re.compile(
    r"帮我约|我要约|我想约|给我约|约一下|订一下|"
    r"取消|退掉|撤销|"
    r"我的预约|我约了|查.*预约记录|我有哪些"
)


def pick_tool_names(text: str) -> list[str]:
    """给一句用户输入，返回该挂哪些工具名。

    保守策略：除非能确定是纯问答，否则返回全集。
    """
    t = (text or "").strip()
    if not t:
        return ALL_TOOL_NAMES

    # 有动作意图 → 全集（可能要下单，也可能夹着问规则）
    if _ACTION_PATTERN.search(t):
        return ALL_TOOL_NAMES

    # 明确在问知识 → 只挂问答组
    if _ASK_PATTERN.search(t):
        return ASK

    # 判断不了 → 全集
    return ALL_TOOL_NAMES
