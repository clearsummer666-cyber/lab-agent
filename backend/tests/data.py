"""假数据层。真项目里这层是 MySQL。

字段对齐原项目 backend/app/models/：
    Lab        id, name, location, capacity, open, status
    Equipment  id, name, lab_id, spec, qty, status

status 约定：1=正常/可用，0=关闭/维修
"""

LABS = [
    {"id": 1, "name": "计算机实验室", "location": "A栋301", "capacity": 40,
     "open": "08:00-21:00", "status": 1},
    {"id": 2, "name": "化学实验室", "location": "B栋102", "capacity": 24,
     "open": "08:30-18:00", "status": 1},
    {"id": 3, "name": "物理实验室", "location": "C栋205", "capacity": 30,
     "open": "08:00-20:00", "status": 1},
    {"id": 4, "name": "生物实验室", "location": "B栋305", "capacity": 16,
     "open": "09:00-17:00", "status": 0},  # 已关闭
]

EQUIPMENTS = [
    {"id": 1, "name": "电子显微镜", "lab_id": 2, "spec": "Zeiss Sigma 300",
     "qty": 2, "status": 1},
    {"id": 2, "name": "离心机", "lab_id": 2, "spec": "Eppendorf 5424R",
     "qty": 4, "status": 1},
    {"id": 3, "name": "示波器", "lab_id": 3, "spec": "Tektronix TBS2000",
     "qty": 6, "status": 1},
    {"id": 4, "name": "3D打印机", "lab_id": 1, "spec": "Bambu X1C",
     "qty": 3, "status": 1},
    {"id": 5, "name": "光谱仪", "lab_id": 3, "spec": "Ocean HDX",
     "qty": 1, "status": 0},  # 维修中
]


def find_labs(keyword: str = "", only_open: bool = True) -> list[dict]:
    """按关键词查实验室。only_open=True 只返回开放的。"""
    rows = [x for x in LABS if x["status"] == 1] if only_open else list(LABS)
    if keyword:
        rows = [x for x in rows if keyword in x["name"] or keyword in x["location"]]
    return rows


def find_equipments(lab_id: int) -> list[dict]:
    """按 lab_id 查设备。"""
    return [x for x in EQUIPMENTS if x["lab_id"] == lab_id]


def get_equipment(equipment_id: int) -> dict | None:
    """按 id 查单个设备。"""
    return next((x for x in EQUIPMENTS if x["id"] == equipment_id), None)


def get_lab(lab_id: int) -> dict | None:
    """按 id 查单个实验室，查不到返回 None。"""
    return next((x for x in LABS if x["id"] == lab_id), None)


# ── 预约记录（真项目里这是 reservations 表）──────────────
# status 对齐原项目 Reservation.status：
#   0=待审核  1=已通过  2=已拒绝  3=已取消
STATUS_TEXT = {0: "待审核", 1: "已通过", 2: "已拒绝", 3: "已取消"}

BOOKINGS = [
    {"id": 1, "lab_id": 2, "date": "2026-10-24", "start": "09:00",
     "end": "11:00", "people": 20, "user": "张老师", "status": 1},
    {"id": 2, "lab_id": 3, "date": "2026-10-24", "start": "14:00",
     "end": "16:00", "people": 25, "user": "李同学", "status": 0},
    {"id": 3, "lab_id": 2, "date": "2026-10-25", "start": "10:00",
     "end": "12:00", "people": 10, "user": "王老师", "status": 1},
]

OPEN_HOUR, CLOSE_HOUR = 8, 21


def get_reservation(rid: int) -> dict | None:
    return next((x for x in BOOKINGS if x["id"] == rid), None)


def list_user_reservations(user: str, status: int | None = None) -> list[dict]:
    """某个用户的预约。status 为 None 表示全部。"""
    rows = [x for x in BOOKINGS if x["user"] == user]
    if status is not None:
        rows = [x for x in rows if x["status"] == status]
    return sorted(rows, key=lambda x: (x["date"], x["start"]))


def cancel_reservation(rid: int, user: str) -> tuple[bool, str]:
    """取消预约。对齐原项目规则：只有待审核/已通过能取消。"""
    row = get_reservation(rid)
    if row is None or row["user"] != user:
        return False, f"没有找到单号 {rid} 的预约"
    if row["status"] == 3:
        return False, f"单号 {rid} 已经是取消状态"
    if row["status"] not in (0, 1):
        return False, f"单号 {rid} 当前是{STATUS_TEXT[row['status']]}，不能取消"
    row["status"] = 3
    return True, f"单号 {rid} 已取消"


# ── 知识库（真项目里这是 RAG 检索，这里用关键词匹配占位）──────
KB_DOCS = [
    {
        "title": "实验室开放时间",
        "content": "计算机实验室 08:00-21:00；化学实验室 08:30-18:00；"
                   "物理实验室 08:00-20:00；生物实验室暂停开放（设备检修）。",
    },
    {
        "title": "预约规则",
        "content": "预约需填写实验室、日期、起止时间和使用人数。提交后状态为待审核，"
                   "管理员确认后方可使用。同一时段不可重复预约。人数不得超过实验室容量。",
    },
    {
        "title": "取消规则",
        "content": "待审核和已通过的预约均可取消。已通过且实验已开场的预约不能取消。",
    },
    {
        "title": "安全须知",
        "content": "进入实验室须穿实验服，禁止饮食。使用高温设备须有人值守。"
                   "离开前检查水电气，关闭门窗。",
    },
    {
        "title": "设备借用",
        "content": "电子显微镜和离心机需提前一天预约并登记使用人。"
                   "光谱仪维修中，暂不可用。",
    },
]


def search_docs(query: str) -> str:
    """极简检索：按关键词命中，返回最相关的一两段。"""
    hits = []
    for doc in KB_DOCS:
        score = sum(1 for ch in set(query) if ch in doc["title"] + doc["content"])
        if score:
            hits.append((score, doc))
    if not hits:
        return "知识库里没有找到相关内容。"
    hits.sort(key=lambda x: -x[0])
    return "\n\n".join(f"【{d['title']}】{d['content']}" for _, d in hits[:2])


def get_bookings(lab_id: int, date: str) -> list[dict]:
    """某个实验室某天的预约记录，按开始时间排序。"""
    rows = [x for x in BOOKINGS if x["lab_id"] == lab_id and x["date"] == date]
    return sorted(rows, key=lambda x: x["start"])


def free_slots(lab_id: int, date: str) -> list[tuple[str, str]]:
    """某天某个实验室的空闲时段。用开放时段减去已占用时段。"""
    busy = get_bookings(lab_id, date)
    free, cursor = [], f"{OPEN_HOUR:02d}:00"
    for b in busy:
        if b["start"] > cursor:
            free.append((cursor, b["start"]))
        cursor = max(cursor, b["end"])
    if cursor < f"{CLOSE_HOUR:02d}:00":
        free.append((cursor, f"{CLOSE_HOUR:02d}:00"))
    return free


def is_available(lab_id: int, date: str, start: str, end: str) -> bool:
    """指定时段能不能约。跟已有预约有任何重叠就算不行。"""
    for b in get_bookings(lab_id, date):
        if start < b["end"] and b["start"] < end:      # 区间重叠
            return False
    return True


def list_availability(date: str, min_capacity: int = 0) -> list[dict]:
    """某天所有开放实验室的占用情况，给"咨询模式"用。"""
    out = []
    for lab in LABS:
        if lab["status"] != 1 or lab["capacity"] < min_capacity:
            continue
        busy = get_bookings(lab["id"], date)
        out.append({
            "lab": lab,
            "busy": busy,
            "free": free_slots(lab["id"], date),
        })
    return out
