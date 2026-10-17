import base64
import hashlib
import json
import os

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.api import webhook
from app.main import app
from app.services import setting_service

KEY = "unit-test-encrypt-key"


def feishu_encrypt(plain: dict, encrypt_key: str) -> str:
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    data = json.dumps(plain).encode()
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + enc.update(data) + enc.finalize()).decode()


@pytest.fixture
async def client(session):
    await setting_service.save_settings(session, {
        "feishu_encrypt_key": KEY,
        "feishu_verification_token": "vt-1",
    })
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    from app.db.models import Setting
    for k in ("feishu_encrypt_key", "feishu_verification_token"):
        row = await session.get(Setting, k)
        if row:
            await session.delete(row)
    await session.commit()
    setting_service.invalidate()


async def test_encrypted_url_verification_challenge(client):
    inner = {"type": "url_verification", "challenge": "ch-9527", "token": "vt-1"}
    resp = await client.post("/feishu/events", json={"encrypt": feishu_encrypt(inner, KEY)})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "ch-9527"}


async def test_encrypted_challenge_wrong_token_401(client):
    inner = {"type": "url_verification", "challenge": "x", "token": "bad"}
    resp = await client.post("/feishu/events", json={"encrypt": feishu_encrypt(inner, KEY)})
    assert resp.status_code == 401


async def test_encrypted_message_event(client):
    inner = {
        "header": {"event_id": "evt-enc-1", "event_type": "im.message.receive_v1", "token": "vt-1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_enc"}},
            "message": {"message_type": "text", "chat_id": "oc_1",
                        "content": '{"text": "你好"}'},
        },
    }
    resp = await client.post("/feishu/events", json={"encrypt": feishu_encrypt(inner, KEY)})
    assert resp.status_code == 200
    item = webhook.queue.get_nowait()
    webhook.queue.task_done()
    assert item["text"] == "你好"


async def test_undecryptable_payload_400(client):
    resp = await client.post("/feishu/events",
                             json={"encrypt": base64.b64encode(os.urandom(64)).decode()})
    assert resp.status_code == 400
