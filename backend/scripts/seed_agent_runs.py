"""给 Agent 运营后台灌演示数据。

用途：面试演示时页面不能是空的。
**这些是造出来的演示数据，不是真实调用**——所以在 question 里保留了真实问法，
但 token / 耗时是按经验值随机的，不要当成性能指标引用。

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/seed_agent_runs.py            # 追加 30 条
    ./.venv/Scripts/python.exe scripts/seed_agent_runs.py --reset     # 先清空再灌
"""

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.agent_run import AgentRun  # noqa: E402
from app.models.user import User  # noqa: E402

# (用户问法, 调用的工具序列, 失败原因 or None)
SAMPLES = [
    ("明天化学分析实验室几点有空？", ["search_lab_docs", "list_availability"], None),
    ("帮我约明天下午两点的示波器", ["get_today", "list_open_labs", "list_lab_equipments", "save_slot"], None),
    ("确认预约", ["create_lab_reservation"], None),
    ("预约规则是什么？能约多久？", ["search_lab_docs"], None),
    ("我现在有哪些预约？", ["list_my_reservations"], None),
    ("取消 id=32 的预约", ["cancel_my_reservation"], None),
    ("人工智能实验室有什么设备", ["list_open_labs", "list_lab_equipments"], None),
    ("这周六物理光学实验室开放吗", ["get_today", "list_availability"], None),
    ("我想约个实验室", ["list_open_labs"], None),
    ("帮我查一下计算机基础实验室今天下午的空闲时段", ["get_today", "list_availability"], None),
    ("把刚才那个预约改到后天", ["get_today", "list_my_reservations", "save_slot"], None),
    ("嵌入式系统实验室能用吗", ["list_open_labs"], None),
    ("预约通过了没", ["list_my_reservations"], None),
    ("设备维修中还能约吗", ["search_lab_docs"], None),
    ("帮我约明天上午十点到十二点的计算机网络实验室", ["get_today", "list_open_labs", "create_lab_reservation"], None),
    ("实验室能带几个人", ["search_lab_docs"], None),
    ("取消我所有的预约", ["list_my_reservations", "cancel_my_reservation"], None),
    ("明天下午有空吗", ["get_today", "list_availability"], None),
    ("帮我约实验室", ["list_open_labs"], None),
    ("安全规范有哪些要求", ["search_lab_docs"], None),
    ("化学分析实验室容量多少", ["list_open_labs", "list_lab_equipments"], None),
    ("帮我约 2030-01-01 的实验室", ["get_today", "create_lab_reservation"], "实验室不存在"),
    ("取消别人的预约", ["cancel_my_reservation"], "无权限操作该预约"),
    ("查一下 id=999 的设备", ["list_lab_equipments"], None),
    ("明天化学分析实验室几点有空？", ["search_lab_docs", "list_availability"], None),
    ("帮我约明天下午的示波器", ["get_today", "list_open_labs", "list_lab_equipments", "save_slot"], None),
    ("我的预约还有效吗", ["list_my_reservations"], None),
    ("物理光学实验室怎么走", ["search_lab_docs"], None),
    ("帮我取消明天下午的预约", ["get_today", "list_my_reservations", "cancel_my_reservation"], None),
    ("周末能预约吗", ["search_lab_docs"], None),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="先清空再灌")
    parser.add_argument("--count", type=int, default=30, help="灌多少条")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.reset:
            deleted = db.query(AgentRun).delete()
            db.commit()
            print(f"已清空 {deleted} 条旧记录")

        # 用真实用户，否则外键过不去
        users = db.query(User).all()
        if not users:
            print("库里没有用户，先跑 seed_data.py")
            return

        now = datetime.now()
        rows = []
        for i in range(args.count):
            question, tools, error = SAMPLES[i % len(SAMPLES)]
            user = users[i % len(users)]
            # 时间铺在最近 7 天，今天多灌一些，趋势图才好看
            days_ago = random.choices([0, 1, 2, 3, 4, 5, 6], weights=[5, 4, 3, 3, 2, 2, 1])[0]
            created = now - timedelta(
                days=days_ago, hours=random.randint(0, 8), minutes=random.randint(0, 59)
            )
            # 工具越多越慢、token 越多——让数字之间有关系，不是纯随机
            tool_count = len(tools)
            duration = random.randint(1200, 2600) + tool_count * random.randint(700, 1500)
            # 分段耗时：模型调用是大头（约 65–80%），工具是零头——和实测结论一致
            model_ratio = random.uniform(0.65, 0.80)
            model_ms = int(duration * model_ratio)
            rows.append(
                AgentRun(
                    user_id=user.id,
                    thread_id=f"user:{user.id}:s0",
                    question=question,
                    status=1 if error else 0,
                    duration_ms=duration,
                    model_ms=model_ms,
                    tool_ms=duration - model_ms,
                    tool_calls=",".join(tools),
                    tool_count=tool_count,
                    input_tokens=random.randint(900, 1800) + tool_count * random.randint(60, 140),
                    output_tokens=random.randint(60, 260),
                    error=error,
                    create_time=created,
                    update_time=created,
                )
            )
        db.add_all(rows)
        db.commit()
        print(f"已写入 {len(rows)} 条演示数据")
        total = db.query(AgentRun).count()
        print(f"当前 agent_runs 共 {total} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
