from datetime import timedelta

import pytest

from app.scheduler.reminder import next_cron_time, utcnow
from app.services import todo_service


async def test_crud_flow(session):
    when = utcnow() + timedelta(hours=2)
    todo = await todo_service.create_todo(session, "买菜", when, source="明天早上10点提醒我买菜")
    assert todo.id and todo.status == "pending"

    rows = await todo_service.list_todos(session, "pending")
    assert todo.id in [t.id for t in rows]

    new_when = utcnow() + timedelta(hours=5)
    updated = await todo_service.update_todo(session, todo.id, remind_at=new_when)
    assert updated.next_remind_at.replace(microsecond=0) == new_when.replace(microsecond=0)

    done = await todo_service.complete_todo(session, todo.id)
    assert done.status == "done" and done.done_at is not None


async def test_reactivate_done_todo(session):
    todo = await todo_service.create_todo(session, "逛街", utcnow() + timedelta(hours=3))
    await todo_service.complete_todo(session, todo.id)
    revived = await todo_service.update_todo(session, todo.id, status="pending")
    assert revived.status == "pending" and revived.done_at is None and revived.remind_count == 0


async def test_admin_status_patch(session):
    todo = await todo_service.create_todo(session, "标记", utcnow() + timedelta(hours=1))
    out = await todo_service.update_todo(session, todo.id, status="done")
    assert out.status == "done" and out.done_at is not None


async def test_list_includes_recurring_outside_window(session):
    from app.agent.tools import h_list
    from datetime import timezone, timedelta as td
    tz8 = timezone(td(hours=8))
    await todo_service.create_todo(session, "喝水", utcnow() + timedelta(hours=1),
                                   repeat_cron="0 10-21/1 * * *")
    tomorrow = (utcnow() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await h_list(session, {
        "status": "pending",
        "from": tomorrow.astimezone(tz8).isoformat(),
        "to": (tomorrow + timedelta(days=1)).astimezone(tz8).isoformat(),
    })
    assert result["ok"]
    assert "喝水" in [t["title"] for t in result["recurring_todos"]]
    assert "喝水" not in [t["title"] for t in result["todos"]]


async def test_last_reminded_state_and_prompt():
    from app import state
    from app.agent.tools import build_system_prompt

    state.set_last_reminded(7)
    assert state.get_last_reminded() == 7
    prompt = build_system_prompt(utcnow(), [{"id": 7, "title": "出门逛街", "remind_at": utcnow(), "status": "pending"}], 7)
    assert "刚提醒过" in prompt and "出门逛街" in prompt
    prompt2 = build_system_prompt(utcnow(), [], None)
    assert "必须追问" in prompt2

    state.clear_if_matches(8)
    assert state.get_last_reminded() == 7
    state.clear_if_matches(7)
    assert state.get_last_reminded() is None


def test_parse_dt_with_tz():
    from app.services.todo_service import parse_dt
    dt = parse_dt("2026-10-17T10:00:00+08:00")
    assert dt.tzinfo is None
    assert dt.hour == 2  # UTC


def test_next_cron_time():
    base = utcnow()
    nxt = next_cron_time("0 10 * * *", base)
    assert nxt > base and nxt.hour == 2  # 10:00 Asia/Shanghai == 02:00 UTC
