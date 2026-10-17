import httpx
import pytest

from app.api import webhook
from app.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_url_verification(client):
    resp = await client.post("/feishu/events",
                             json={"type": "url_verification", "challenge": "abc123", "token": ""})
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "abc123"


async def test_message_event_enqueued(client):
    payload = {
        "header": {"event_id": "evt-1", "event_type": "im.message.receive_v1", "token": ""},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_webhook"}},
            "message": {"message_type": "text", "chat_id": "oc_9",
                        "content": '{"text": "明早10点提醒我跑步"}'},
        },
    }
    resp = await client.post("/feishu/events", json=payload)
    assert resp.status_code == 200
    item = webhook.queue.get_nowait()
    assert item["open_id"] == "ou_webhook" and "跑步" in item["text"]

    # 重复 event_id 去重
    webhook.queue.task_done()
    resp2 = await client.post("/feishu/events", json=payload)
    assert resp2.json().get("duplicated") is True


async def test_healthz(client):
    assert (await client.get("/healthz")).json() == {"ok": True}


async def test_bad_body_returns_400(client):
    assert (await client.post("/feishu/events", content=b"")).status_code == 400
    assert (await client.post("/feishu/events", content=b"not json")).status_code == 400
    assert (await client.post("/feishu/events", json=[1, 2])).status_code == 400
