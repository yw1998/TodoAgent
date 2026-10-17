import json
from datetime import timedelta

import httpx
import pytest

from app.api.card_actions import extract_action, handle_card_action
from app.main import app
from app.scheduler.reminder import utcnow
from app.services import todo_service, user_service


def v2_payload(open_id: str, value: dict, event_id="evt-card-1") -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger", "token": ""},
        "event": {"operator": {"open_id": open_id},
                  "action": {"tag": "button", "value": value}},
    }


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
async def todo_setup(session):
    user = await user_service.upsert_user(session, "feishu", "ou_btn", "按钮君", "oc_1")
    todo = await todo_service.create_todo(session, user, "取快递", utcnow() + timedelta(hours=1))
    return user, todo


async def test_button_complete(session, todo_setup):
    user, todo = todo_setup
    resp = await handle_card_action(v2_payload("ou_btn", {"act": "complete", "todo_id": str(todo.id)}))
    assert resp["toast"]["type"] == "success"
    card = json.loads(resp["card"]["content"])
    assert "已完成" in card["header"]["title"]["content"]
    fresh = await todo_service.get_user_todo(session, user, todo.id)
    await session.refresh(fresh)
    assert fresh.status == "done"


async def test_button_snooze(session, todo_setup):
    user, todo = todo_setup
    before = utcnow() + timedelta(minutes=30)
    resp = await handle_card_action(v2_payload("ou_btn", {"act": "snooze", "todo_id": str(todo.id), "minutes": "30"}))
    assert "延后" in resp["toast"]["content"]
    fresh = await todo_service.get_user_todo(session, user, todo.id)
    await session.refresh(fresh)
    assert fresh.status == "pending"
    assert abs((fresh.next_remind_at - before).total_seconds()) < 120


async def test_button_other_user_rejected(session, todo_setup):
    _, todo = todo_setup
    resp = await handle_card_action(v2_payload("ou_stranger", {"act": "complete", "todo_id": str(todo.id)}))
    assert resp["toast"]["type"] == "error"
    assert "card" not in resp


async def test_legacy_action_extract():
    legacy = {"type": "action", "content": json.dumps({
        "action": {"tag": "button", "value": {"act": "complete", "todo_id": "9"}},
        "operator": {"open_id": "ou_x"}})}
    value, operator = extract_action(legacy)
    assert value["act"] == "complete" and operator == "ou_x"


async def test_route_returns_card_update(client, session, todo_setup):
    _, todo = todo_setup
    resp = await client.post("/feishu/events",
                             json=v2_payload("ou_btn", {"act": "complete", "todo_id": str(todo.id)},
                                             event_id="evt-route-1"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["toast"]["type"] == "success"
    assert "已完成" in json.loads(body["card"]["content"])["header"]["title"]["content"]
