"""预约业务的核心约束。

这里测的是**真风险点**，不是凑覆盖率：
区间重叠的四种形态、四态状态机的非法流转、越权、唯一索引兜底。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import BusinessException
from app.models.reservation import Reservation
from app.schemas.reservation import ReservationCreateRequest
from app.services import reservation_service as svc


def _req(lab, date: str, start: str, end: str, **kwargs) -> ReservationCreateRequest:
    return ReservationCreateRequest(
        lab_id=lab.id, date=date, start_time=start, end_time=end, **kwargs
    )


# ───────────────────────── 区间重叠 ─────────────────────────


@pytest.mark.parametrize(
    ("start", "end", "should_conflict"),
    [
        ("09:00", "13:00", True),   # 新时段包含已有预约
        ("10:30", "11:00", True),   # 新时段被已有预约包含
        ("09:00", "10:30", True),   # 左交
        ("11:30", "13:00", True),   # 右交
        ("12:00", "14:00", False),  # 右相切：端点相接不算重叠
        ("08:00", "10:00", False),  # 左相切
        ("14:00", "16:00", False),  # 完全分离
    ],
)
def test_overlap_matrix(
    db, make_user, make_lab, make_reservation, tomorrow, start, end, should_conflict
):
    """已有 [10:00, 12:00)，逐种情况验证 `start < new_end AND end > new_start`。"""
    user = make_user()
    lab = make_lab()
    make_reservation(user, lab, tomorrow, "10:00", "12:00", status=1)

    if should_conflict:
        with pytest.raises(BusinessException, match="该时段已预约"):
            svc.create_reservation(db, user, _req(lab, tomorrow, start, end))
    else:
        svc.create_reservation(db, user, _req(lab, tomorrow, start, end))
        total = db.query(Reservation).count()
        assert total == 2


def test_cancelled_reservation_does_not_block(
    db, make_user, make_lab, make_reservation, tomorrow
):
    """已取消(3) / 已拒绝(2) 的预约不占用时段，可以重新约同一时段。"""
    user = make_user()
    lab = make_lab()
    make_reservation(user, lab, tomorrow, "10:00", "12:00", status=3)
    make_reservation(user, lab, tomorrow, "10:00", "12:00", status=2)

    svc.create_reservation(db, user, _req(lab, tomorrow, "10:00", "12:00"))
    assert db.query(Reservation).filter(Reservation.status == 0).count() == 1


def test_different_lab_same_time_is_fine(db, make_user, make_lab, tomorrow):
    """不同实验室同一时段互不影响 —— 冲突判定必须带 lab_id。"""
    user = make_user()
    lab_a = make_lab(name="实验室A")
    lab_b = make_lab(name="实验室B")

    svc.create_reservation(db, user, _req(lab_a, tomorrow, "10:00", "12:00"))
    svc.create_reservation(db, user, _req(lab_b, tomorrow, "10:00", "12:00"))
    assert db.query(Reservation).count() == 2


# ───────────────────────── 时间与参数校验 ─────────────────────────


def test_time_is_normalized_to_two_digits(db, make_user, make_lab, tomorrow):
    """`9:00` 要归一化成 `09:00`，否则字符串比较会得出 "9:00" > "10:00"。"""
    user = make_user()
    lab = make_lab()
    svc.create_reservation(db, user, _req(lab, tomorrow, "9:00", "11:30"))

    item = db.query(Reservation).one()
    assert item.start_time == "09:00"
    assert item.end_time == "11:30"


@pytest.mark.parametrize("bad", ["9点", "09-00", "", "24:00", "10:60"])
def test_invalid_time_format_rejected(db, make_user, make_lab, tomorrow, bad):
    user = make_user()
    lab = make_lab()
    with pytest.raises(BusinessException):
        svc.create_reservation(db, user, _req(lab, tomorrow, bad, "12:00"))


def test_end_must_be_after_start(db, make_user, make_lab, tomorrow):
    user = make_user()
    lab = make_lab()
    with pytest.raises(BusinessException, match="结束时间必须晚于开始时间"):
        svc.create_reservation(db, user, _req(lab, tomorrow, "12:00", "12:00"))


def test_past_date_rejected(db, make_user, make_lab, yesterday):
    user = make_user()
    lab = make_lab()
    with pytest.raises(BusinessException, match="不能小于当前的日期"):
        svc.create_reservation(db, user, _req(lab, yesterday, "10:00", "12:00"))


def test_lab_not_found(db, make_user, tomorrow):
    user = make_user()
    req = ReservationCreateRequest(
        lab_id=99999, date=tomorrow, start_time="10:00", end_time="12:00"
    )
    with pytest.raises(BusinessException, match="实验室不存在"):
        svc.create_reservation(db, user, req)


def test_closed_lab_rejected(db, make_user, make_lab, tomorrow):
    user = make_user()
    lab = make_lab(status=0)
    with pytest.raises(BusinessException, match="实验室已关闭"):
        svc.create_reservation(db, user, _req(lab, tomorrow, "10:00", "12:00"))


@pytest.mark.parametrize(
    ("start", "end", "msg"),
    [
        ("07:00", "09:00", "不能早于实验室的开放时间"),
        ("21:00", "23:00", "不能晚于实验室的关闭时间"),
    ],
)
def test_outside_open_hours(db, make_user, make_lab, tomorrow, start, end, msg):
    user = make_user()
    lab = make_lab(open_time="08:00", close_time="22:00")
    with pytest.raises(BusinessException, match=msg):
        svc.create_reservation(db, user, _req(lab, tomorrow, start, end))


def test_capacity_exceeded(db, make_user, make_lab, tomorrow):
    """人数超容量要拦 —— 这是 2026-10-25 补的缺口（原 REST 接口能约进超容量）。"""
    user = make_user()
    lab = make_lab(capacity=30)
    with pytest.raises(BusinessException, match="最多容纳 30 人"):
        svc.create_reservation(db, user, _req(lab, tomorrow, "10:00", "12:00", people=31))


def test_capacity_none_skips_check(db, make_user, make_lab, tomorrow):
    """前端不传 people 时跳过容量校验（向后兼容）。"""
    user = make_user()
    lab = make_lab(capacity=30)
    svc.create_reservation(db, user, _req(lab, tomorrow, "10:00", "12:00"))
    assert db.query(Reservation).count() == 1


def test_equipment_under_repair_rejected(
    db, make_user, make_lab, make_equipment, tomorrow
):
    user = make_user()
    lab = make_lab()
    equipment = make_equipment(lab, status=0)
    with pytest.raises(BusinessException, match="正在维修"):
        svc.create_reservation(
            db, user, _req(lab, tomorrow, "10:00", "12:00", equipment_id=equipment.id)
        )


@pytest.mark.xfail(
    strict=True,
    reason="已知缺口：service 层没校验设备是否属于该实验室（工具层拦了，REST 接口没拦）",
)
def test_equipment_must_belong_to_lab(
    db, make_user, make_lab, make_equipment, tomorrow
):
    """约 A 实验室的时段却挂 B 实验室的设备 —— 应该被拒绝。"""
    user = make_user()
    lab_a = make_lab(name="实验室A")
    lab_b = make_lab(name="实验室B")
    equipment = make_equipment(lab_b, name="B 的示波器")

    with pytest.raises(BusinessException):
        svc.create_reservation(
            db, user, _req(lab_a, tomorrow, "10:00", "12:00", equipment_id=equipment.id)
        )


# ───────────────────────── 唯一索引兜底 ─────────────────────────


def test_exact_duplicate_hits_unique_index(
    db, make_user, make_lab, make_equipment, tomorrow
):
    """绕过 service 直接写库时，唯一索引 `uk_reservation_exact` 必须挡住。"""
    user = make_user()
    lab = make_lab()
    equipment = make_equipment(lab)
    fields = dict(
        user_id=user.id,
        lab_id=lab.id,
        equipment_id=equipment.id,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
        status=0,
    )

    db.add(Reservation(**fields))
    db.commit()

    db.add(Reservation(**fields))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.xfail(
    strict=True,
    reason="已知取舍：equipment_id 为 NULL 时唯一索引不生效（NULL 互不相等），只约实验室靠行锁",
)
def test_exact_duplicate_without_equipment_hits_unique_index(
    db, make_user, make_lab, tomorrow
):
    user = make_user()
    lab = make_lab()
    fields = dict(
        user_id=user.id,
        lab_id=lab.id,
        equipment_id=None,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
        status=0,
    )
    db.add(Reservation(**fields))
    db.commit()
    db.add(Reservation(**fields))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ───────────────────────── 取消状态机 ─────────────────────────


@pytest.mark.parametrize("status", [2, 3])
def test_cannot_cancel_finished_states(
    db, make_user, make_lab, make_reservation, tomorrow, status
):
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, tomorrow, status=status)
    with pytest.raises(BusinessException, match="当前状态无法取消"):
        svc.cancel_reservation(db, user, item.id)


def test_cannot_cancel_others_reservation(
    db, make_user, make_lab, make_reservation, tomorrow
):
    """越权取消 —— 必须返回 403 业务码，不能只是文案。"""
    owner = make_user(username="owner")
    intruder = make_user(username="intruder")
    lab = make_lab()
    item = make_reservation(owner, lab, tomorrow, status=0)

    with pytest.raises(BusinessException) as exc_info:
        svc.cancel_reservation(db, intruder, item.id)
    assert exc_info.value.code == 403


def test_cannot_cancel_approved_reservation_already_started(
    db, make_user, make_lab, make_reservation, yesterday
):
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, yesterday, status=1)
    with pytest.raises(BusinessException, match="实验已开始或已结束"):
        svc.cancel_reservation(db, user, item.id)


@pytest.mark.parametrize("status", [0, 1])
def test_cancel_future_reservation_ok(
    db, make_user, make_lab, make_reservation, tomorrow, status
):
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, tomorrow, status=status)

    svc.cancel_reservation(db, user, item.id)
    db.refresh(item)
    assert item.status == 3


# ───────────────────────── 审核状态机 ─────────────────────────


@pytest.mark.parametrize("bad_status", [0, 3, 99])
def test_audit_rejects_invalid_target_status(db, make_user, make_lab, make_reservation, tomorrow, bad_status):
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, tomorrow, status=0)
    with pytest.raises(BusinessException, match="审核状态错误"):
        svc.audit_reservation(db, item.id, bad_status)


@pytest.mark.parametrize("status", [1, 2, 3])
def test_audit_only_from_pending(
    db, make_user, make_lab, make_reservation, tomorrow, status
):
    """只有待审核(0) 能审核，其余状态一律拒绝。"""
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, tomorrow, status=status)
    with pytest.raises(BusinessException, match="当前状态不支持审核"):
        svc.audit_reservation(db, item.id, 1)


@pytest.mark.parametrize("target", [1, 2])
def test_audit_pending_ok(db, make_user, make_lab, make_reservation, tomorrow, target):
    user = make_user()
    lab = make_lab()
    item = make_reservation(user, lab, tomorrow, status=0)

    svc.audit_reservation(db, item.id, target)
    db.refresh(item)
    assert item.status == target


# ───────────────────────── 列表可见性 ─────────────────────────


def test_student_only_sees_own_reservations(
    db, make_user, make_lab, make_reservation, tomorrow
):
    alice = make_user(username="alice")
    bob = make_user(username="bob")
    admin = make_user(username="admin", role="admin")
    lab = make_lab()
    make_reservation(alice, lab, tomorrow, "10:00", "11:00")
    make_reservation(bob, lab, tomorrow, "11:00", "12:00")

    alice_page = svc.get_reservation_page_list(db, alice, page=1, page_size=10)
    admin_page = svc.get_reservation_page_list(db, admin, page=1, page_size=10)

    assert alice_page.total == 1
    assert alice_page.list[0].user_name == "alice"
    assert admin_page.total == 2
