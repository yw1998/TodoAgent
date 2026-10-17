import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from app.api.card_actions import extract_action, handle_card_action
from app.channels.feishu import FeishuChannel
from app.main import app
from app.scheduler.reminder import utcnow
from app.services import todo_service


def v2_payload(value: dict, event_id="evt-card-1") -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger", "token": ""},
        "event": {"operator": {"open_id": "ou_btn"},
                  "action": {"tag": "button", "value": value}},
    }


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
async def todo_setup(session):
    return await todo_service.create_todo(session, "取快递", utcnow() + timedelta(hours=1))


async def test_button_complete(session, todo_setup):
    todo = todo_setup
    resp = await handle_card_action(v2_payload({"act": "complete", "todo_id": str(todo.id)}))
    assert resp["toast"]["type"] == "success"
    await asyncio.sleep(0.05)  # 排空补发确认消息的后台任务
    card = json.loads(resp["card"]["content"])
    assert "已完成" in card["header"]["title"]["content"]
    fresh = await todo_service.get_todo(session, todo.id)
    await session.refresh(fresh)
    assert fresh.status == "done"


async def test_button_snooze(session, todo_setup):
    todo = todo_setup
    before = utcnow() + timedelta(minutes=30)
    resp = await handle_card_action(v2_payload({"act": "snooze", "todo_id": str(todo.id), "minutes": "30"}))
    assert "延后" in resp["toast"]["content"]
    await asyncio.sleep(0.05)
    fresh = await todo_service.get_todo(session, todo.id)
    await session.refresh(fresh)
    assert fresh.status == "pending"
    assert abs((fresh.next_remind_at - before).total_seconds()) < 120


async def test_button_missing_todo(session):
    resp = await handle_card_action(v2_payload({"act": "complete", "todo_id": "99999"}))
    assert resp["toast"]["type"] == "error"
    assert "card" not in resp


async def test_legacy_action_extract():
    legacy = {"type": "action", "content": json.dumps({
        "action": {"tag": "button", "value": {"act": "complete", "todo_id": "9"}},
        "operator": {"open_id": "ou_x"}})}
    value = extract_action(legacy)
    assert value["act"] == "complete"


async def test_button_complete_sends_reply(session, todo_setup, monkeypatch):
    todo = todo_setup
    sent: list[dict] = []

    async def fake(self, s, card):
        sent.append(card)
    monkeypatch.setattr(FeishuChannel, "send_card_message", fake)

    resp = await handle_card_action(v2_payload({"act": "complete", "todo_id": str(todo.id)}))
    assert resp["toast"]["type"] == "success"
    await asyncio.sleep(0.05)  # 排空补发确认消息的后台任务
    await asyncio.sleep(0.05)  # 等待补发任务
    assert sent and "已完成" in sent[0]["header"]["title"]["content"]


async def test_route_returns_card_update(client, session, todo_setup):
    todo = todo_setup
    resp = await client.post("/feishu/events",
                             json=v2_payload({"act": "complete", "todo_id": str(todo.id)},
                                             event_id="evt-route-1"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["toast"]["type"] == "success"
    await asyncio.sleep(0.05)
    assert "已完成" in json.loads(body["card"]["content"])["header"]["title"]["content"]
