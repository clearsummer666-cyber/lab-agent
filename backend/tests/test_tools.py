"""Agent 工具层：失败降级、参数不臆造、跨用户隔离。

这些是「让模型不乱来」的代码级防线，比 prompt 约束更可靠 ——
prompt 会被模型忽略，代码不会。
"""

import datetime
import json

import pytest

from app.ai.repo import SqlRepo
from app.ai.tools import make_tools


@pytest.fixture
def agent_tools(db, make_user):
    """返回 (user, {工具名: 工具对象})，repo 已注入当前用户。"""
    user = make_user()
    repo = SqlRepo(db, user_id=user.id)
    return user, {tool.name: tool for tool in make_tools(repo=repo)}


def _call(tool, **kwargs) -> dict:
    """直接调底层函数（跳过 LangChain 的参数校验），把返回值当 JSON 解析。"""
    return json.loads(tool.func(**kwargs))


# ───────────────────────── 参数不臆造 ─────────────────────────


def test_unknown_lab_id_returns_error_not_exception(agent_tools, tomorrow):
    """模型编了个不存在的 lab_id —— 返回结构化错误，不能抛异常打断循环。"""
    _, tools = agent_tools
    result = _call(
        tools["create_lab_reservation"],
        lab_id=99999,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
    )
    assert result["ok"] is False
    assert "99999" in result["error"]


def test_malformed_date_rejected_before_db(agent_tools):
    """模型把「明天」原样传进来时，要在碰数据库之前拦掉。"""
    _, tools = agent_tools
    result = _call(
        tools["create_lab_reservation"],
        lab_id=1,
        date="明天",
        start_time="10:00",
        end_time="12:00",
    )
    assert result["ok"] is False
    assert "YYYY-MM-DD" in result["error"]


def test_capacity_overflow_returns_error(agent_tools, make_lab, tomorrow):
    _, tools = agent_tools
    lab = make_lab(capacity=30)
    result = _call(
        tools["create_lab_reservation"],
        lab_id=lab.id,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
        people=99,
    )
    assert result["ok"] is False
    assert "30" in result["error"]


# ───────────────────────── 业务异常降级 ─────────────────────────


def test_conflict_returns_error_json(agent_tools, make_lab, tomorrow):
    """时段冲突时 service 会抛 BusinessException —— 工具必须转成 JSON 返回。"""
    _, tools = agent_tools
    lab = make_lab()

    first = _call(
        tools["create_lab_reservation"],
        lab_id=lab.id,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
    )
    assert first["ok"] is True

    second = _call(
        tools["create_lab_reservation"],
        lab_id=lab.id,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
    )
    # 无论是幂等提示还是「该时段已预约」，都必须是结构化错误而非抛异常
    assert second["ok"] is False
    assert isinstance(second["error"], str) and second["error"]


def test_get_today_returns_real_date(agent_tools):
    """日期不许模型猜 —— 工具必须返回服务器真实日期。"""
    _, tools = agent_tools
    assert tools["get_today"].func() == str(datetime.date.today())


# ───────────────────────── 跨用户隔离 ─────────────────────────


def test_tools_only_see_own_reservations(db, make_user, make_lab, tomorrow):
    """A 的工具查不到 B 的预约 —— repo 绑的是 user_id，不是随便传。"""
    alice = make_user(username="alice")
    bob = make_user(username="bob")
    lab = make_lab()

    alice_tools = {t.name: t for t in make_tools(repo=SqlRepo(db, user_id=alice.id))}
    bob_tools = {t.name: t for t in make_tools(repo=SqlRepo(db, user_id=bob.id))}

    created = _call(
        alice_tools["create_lab_reservation"],
        lab_id=lab.id,
        date=tomorrow,
        start_time="10:00",
        end_time="12:00",
    )
    assert created["ok"] is True

    assert _call(alice_tools["list_my_reservations"])["total"] == 1
    assert _call(bob_tools["list_my_reservations"])["total"] == 0


def test_cannot_cancel_others_reservation_via_tool(
    db, make_user, make_lab, make_reservation, tomorrow
):
    """B 拿 A 的单号去取消 —— 工具层必须拒绝。"""
    alice = make_user(username="alice")
    bob = make_user(username="bob")
    lab = make_lab()
    item = make_reservation(alice, lab, tomorrow, status=0)

    bob_tools = {t.name: t for t in make_tools(repo=SqlRepo(db, user_id=bob.id))}
    result = _call(bob_tools["cancel_my_reservation"], reservation_id=item.id)

    assert result["ok"] is False
    db.refresh(item)
    assert item.status == 0


# ───────────────────────── 输出格式 ─────────────────────────


def test_no_arg_tools_return_json(agent_tools):
    """无必填参数的工具都必须返回可解析 JSON（get_today 是唯一例外）。"""
    _, tools = agent_tools
    for name in ("list_open_labs", "list_my_reservations"):
        parsed = json.loads(tools[name].func())
        assert isinstance(parsed, dict), f"{name} 返回的不是 JSON 对象"
