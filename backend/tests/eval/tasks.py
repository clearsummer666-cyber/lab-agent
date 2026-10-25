"""黄金集：一条 task = 一道题。

一道题必须写清四件事，缺一件就不是可重复的用例：
    输入            user_says（单轮）或 turns（多轮）
    初始条件        data.BOOKINGS 的初始状态（统一由 harness 重置）
    成功标准        expect
    禁止行为        forbid（护栏，触发即 fail）

30 条主集，按 EVAL.md 的八类专项分组。20-50 条起步就够，别一上来搞几百条。
"""

from eval.core import Task

# expect 支持的键（都是代码能直接断言的，不烧 token）
#   bookings_delta : int   预约记录条数的变化
#   slots_cleared  : bool  提交成功后槽位是否清空
#   must_call      : str   必须调用过的工具
#   must_not_call  : str   不许调用的工具
#   reply_mentions : str   回复里必须出现的词

TODAY = "2026-10-24"
TMR = "2026-10-25"        # 明天；化学实验室当天 10:00-12:00 已被占用
DAY3 = "2026-10-26"       # 后天

GOLDEN: list[Task] = [
    # ══ 1. 核心任务：产品靠它吃饭 ═════════════════════════
    Task(
        id="C01", suite="核心任务",
        user_says="帮我约明天下午2点到4点化学实验室，4个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1, "slots_cleared": True, "must_call": "create_lab_reservation"},
        forbid=["落库重复", "未经确认落库"],
    ),
    Task(
        id="C02", suite="核心任务",
        user_says="帮我约明天下午2点到4点化学实验室，4个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=False,
        expect={"bookings_delta": 0, "slots_cleared": False},
        forbid=["未经确认落库"],
    ),
    Task(
        id="C03", suite="核心任务",
        # ★ user_says 必须自带全部信息：真模型是从这句话里抽的，
        #   看不到 slots 字段（那是给假 Agent 用的）
        user_says="帮我约明天上午10点到11点化学实验室，4个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "10:00", "end": "11:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 0, "reply_mentions": "占"},
        forbid=["落库重复"],
    ),
    Task(
        id="C04", suite="核心任务",
        user_says="算了不约了",
        slots={},
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
        forbid=["未经确认落库"],
    ),

    # ══ 2. 知识问答：该查就查，不查别编 ═══════════════════
    Task(
        id="K01", suite="知识问答",
        user_says="化学实验室有什么设备",
        expect={"bookings_delta": 0, "must_call": "list_lab_equipments",
                "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="K02", suite="知识问答",
        user_says="明天哪些实验室有空",
        expect={"bookings_delta": 0, "must_call": "list_availability",
                "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="K03", suite="知识问答",
        user_says="生物实验室现在能用吗",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),

    # ══ 3. 多轮：指代与改口 ═══════════════════════════════
    Task(
        id="M01", suite="多轮-指代",
        turns=["帮我看看化学实验室明天的情况", "那间呢，下午有位置吗"],
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="M02", suite="多轮-指代",
        turns=["我想约化学实验室", "明天下午吧", "那就2点到4点", "4个人，就这个"],
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1, "must_call": "create_lab_reservation"},
        forbid=["落库重复"],
    ),
    Task(
        id="M03", suite="多轮-改口",
        turns=["帮我约物理实验室明天下午2点到4点", "等等，换成化学实验室"],
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="M04", suite="多轮-指代",
        turns=["物理实验室明天下午有空吗", "那后天呢"],
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),

    # ══ 4. 异常输入：不能被搞乱 ═══════════════════════════
    Task(
        id="E01", suite="异常输入",
        user_says="",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="E02", suite="异常输入",
        user_says="约" * 800,
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="E03", suite="异常输入",
        user_says="asdfghjkl qwerty",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="E04", suite="异常输入",
        user_says="帮我约火星实验室明天下午2点",
        slots={"lab_name": "火星实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 0},
        forbid=["落库重复"],
    ),

    # ══ 5. 工具调用：该调 / 不该调 / 参数对不对 ═══════════
    Task(
        id="T01", suite="工具调用",
        user_says="明天化学实验室几点到几点空着",
        expect={"bookings_delta": 0, "must_call": "list_availability"},
    ),
    Task(
        id="T02", suite="工具调用",
        user_says="实验室开放时间是几点到几点",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="T03", suite="工具调用",
        user_says="帮我约明天下午两点到四点的化学实验室，四个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1, "must_call": "create_lab_reservation"},
        forbid=["落库重复"],
    ),
    Task(
        id="T04", suite="工具调用",
        user_says="明天下午2点到4点化学实验室，我们要30个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 30},
        approve=True,
        expect={"bookings_delta": 0, "reply_mentions": "人"},
        forbid=["落库重复"],
    ),

    # ══ 6. 多意图：一句话两个需求 ═════════════════════════
    Task(
        id="X01", suite="多意图",
        user_says="帮我看看化学实验室明天的情况，另外查下物理实验室有什么设备",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="X02", suite="多意图",
        user_says="我想约明天下午2点化学实验室，顺便问下生物实验室能用吗",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="X03", suite="多意图",
        user_says="化学和物理实验室明天下午都有空吗",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),

    # ══ 7. 模糊意图：该澄清，别脑补 ═══════════════════════
    Task(
        id="A01", suite="模糊意图",
        user_says="帮我约个实验室",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation",
                "reply_mentions": "哪"},
    ),
    Task(
        id="A02", suite="模糊意图",
        user_says="明天下午吧，随便",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="A03", suite="模糊意图",
        user_says="有空位就行，你看着办",
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),

    # ══ 8. 长对话衰减：第几轮开始忘 ═══════════════════════
    Task(
        id="D01", suite="长对话-衰减",
        # 故意不给人数：信息不齐就不会提交，这样才能单独测「记不记得」
        turns=["我想约化学实验室", "明天", "下午", "2点到4点",
               "刚才说的是哪个实验室来着"],
        expect={"bookings_delta": 0, "must_not_call": "create_lab_reservation"},
    ),
    Task(
        id="D02", suite="长对话-衰减",
        turns=["帮我约明天下午2点到4点化学实验室，4个人",
               "先别提交", "我再想想", "对了开放时间几点", "还是那个时段"],
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=False,
        expect={"bookings_delta": 0},
        forbid=["未经确认落库"],
    ),
    Task(
        id="D03", suite="长对话-衰减",
        turns=["我想约化学实验室", "明天吧", "有什么设备", "好，下午2点到4点",
               "4个人", "约吧"],
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1, "must_call": "create_lab_reservation"},
        forbid=["落库重复"],
    ),

    # ══ 9. 幂等：重复提交只落一条 ═════════════════════════
    Task(
        id="I01", suite="幂等",
        turns=["帮我约明天下午2点到4点化学实验室，4个人",
               "就这个，提交吧", "再提交一次"],
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1},
        forbid=["落库重复"],
    ),
    Task(
        id="I02", suite="幂等",
        user_says="帮我约明天下午2点到4点化学实验室，4个人",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=True,
        expect={"bookings_delta": 1},
        forbid=["落库重复"],
    ),
]


# ── 专门用来验护栏的用例（故意让被测对象违规）──────────────
GUARDRAIL_CASES: list[Task] = [
    Task(
        id="G01", suite="护栏-未确认落库",
        user_says="帮我约明天下午2点到4点化学实验室",
        slots={"lab_name": "化学实验室", "book_date": TMR,
               "start": "14:00", "end": "16:00", "people": 4},
        approve=False,
        expect={"bookings_delta": 1},
        forbid=["未经确认落库"],
    ),
]

# 关键路径：必须用 pass^k 反复跑的用例
CRITICAL = ["C01", "C02", "C03", "T04", "I01"]
