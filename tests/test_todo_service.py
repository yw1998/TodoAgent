from datetime import timedelta

import pytest

from app.scheduler.reminder import next_cron_time, utcnow
from app.services import todo_service, user_service


async def _mk_user(session, ext="ou_a"):
    return await user_service.upsert_user(session, "feishu", ext, "Test", "oc_chat")


async def test_crud_flow(session):
    user = await _mk_user(session)
    when = utcnow() + timedelta(hours=2)
    todo = await todo_service.create_todo(session, user, "买菜", when, source="明天早上10点提醒我买菜")
    assert todo.id and todo.status == "pending"

    rows = await todo_service.list_todos(session, user, "pending")
    assert len(rows) == 1

    new_when = utcnow() + timedelta(hours=5)
    updated = await todo_service.update_todo(session, user, todo.id, remind_at=new_when)
    assert updated.next_remind_at.replace(microsecond=0) == new_when.replace(microsecond=0)

    done = await todo_service.complete_todo(session, user, todo.id)
    assert done.status == "done" and done.done_at is not None


async def test_user_isolation(session):
    u1 = await _mk_user(session, "ou_1")
    u2 = await _mk_user(session, "ou_2")
    todo = await todo_service.create_todo(session, u1, "私密", utcnow() + timedelta(hours=1))
    assert await todo_service.get_user_todo(session, u2, todo.id) is None
    assert await todo_service.complete_todo(session, u2, todo.id) is None
    assert await todo_service.delete_todo(session, u2, todo.id) is False


def test_parse_dt_with_tz():
    from app.services.todo_service import parse_dt
    dt = parse_dt("2026-10-17T10:00:00+08:00")
    assert dt.tzinfo is None
    assert dt.hour == 2  # UTC


def test_next_cron_time():
    base = utcnow()
    nxt = next_cron_time("0 10 * * *", base)
    assert nxt > base and nxt.hour == 2  # 10:00 Asia/Shanghai == 02:00 UTC


async def test_reactivate_done_todo(session):
    user = await _mk_user(session, "ou_re")
    todo = await todo_service.create_todo(session, user, "逛街", utcnow() + timedelta(hours=3))
    await todo_service.complete_todo(session, user, todo.id)
    revived = await todo_service.update_todo(session, user, todo.id, status="pending")
    assert revived.status == "pending" and revived.done_at is None and revived.remind_count == 0
    assert await todo_service.update_todo(session, user, todo.id, title="x") is not None


async def test_last_reminded_state_and_prompt():
    from app import state
    from app.agent.tools import build_system_prompt
    from datetime import timezone

    state.set_last_reminded(999, 7)
    assert state.get_last_reminded(999) == 7
    prompt = build_system_prompt(utcnow().replace(tzinfo=None),
                                 [{"id": 7, "title": "出门逛街", "remind_at": utcnow()}], 7)
    assert "刚提醒过" in prompt and "出门逛街" in prompt
    prompt2 = build_system_prompt(utcnow().replace(tzinfo=None), [], None)
    assert "必须追问" in prompt2

    state.clear_if_matches(999, 8)  # 不匹配的 id 不清除
    assert state.get_last_reminded(999) == 7
    state.clear_if_matches(999, 7)
    assert state.get_last_reminded(999) is None
