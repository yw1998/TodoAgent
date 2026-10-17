"""飞书事件回调：验签/解密、去重、快速 ACK、投递队列由后台 worker 消费。"""

import asyncio
import json
import time
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.db.models import MessageLog
from app.db.session import SessionLocal
from app.services import setting_service

router = APIRouter()

queue: asyncio.Queue = asyncio.Queue()
_seen: "OrderedDict[str, float]" = OrderedDict()
_seen_ttl = 300


def _dedup(event_id: str) -> bool:
    now = time.time()
    while _seen and next(iter(_seen.values())) < now - _seen_ttl:
        _seen.popitem(last=False)
    if event_id in _seen:
        return True
    _seen[event_id] = now
    return False


async def _verify_token(token: str) -> None:
    async with SessionLocal() as session:
        expected = await setting_service.get_setting(session, "feishu_verification_token")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="invalid verification token")


@router.post("/feishu/events")
async def feishu_events(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid event payload")

    if "encrypt" in payload:
        async with SessionLocal() as session:
            encrypt_key = await setting_service.get_setting(session, "feishu_encrypt_key")
        if not encrypt_key:
            raise HTTPException(status_code=400,
                                detail="payload encrypted: set feishu_encrypt_key in .env or admin settings")
        from app.channels.feishu import decrypt_event
        try:
            payload = decrypt_event(payload["encrypt"], encrypt_key)
        except Exception:
            raise HTTPException(status_code=400, detail="decrypt failed (wrong encrypt_key?)")

    if payload.get("type") == "url_verification":
        await _verify_token(payload.get("token", ""))
        return {"challenge": payload.get("challenge", "")}

    header = payload.get("header", {})
    if header.get("event_type") != "im.message.receive_v1":
        return {"code": 0}
    await _verify_token(header.get("token", ""))

    event_id = header.get("event_id", "")
    if event_id and _dedup(event_id):
        return {"code": 0, "duplicated": True}

    event = payload.get("event", {})
    msg = event.get("message", {})
    if msg.get("message_type") not in ("text", "post"):
        return {"code": 0}
    try:
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
    except json.JSONDecodeError:
        text = ""
    open_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    if not text or not open_id:
        return {"code": 0}

    await queue.put({
        "open_id": open_id,
        "chat_id": msg.get("chat_id", ""),
        "text": text,
    })
    return {"code": 0}  # 快速 ACK，3s 限制


async def worker_loop(concurrency: int) -> None:
    async def worker():
        while True:
            item = await queue.get()
            try:
                await process_message(item)
            except Exception as e:
                import logging
                logging.getLogger("todoagent").exception(f"worker error: {e}")
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    return tasks


async def process_message(item: dict) -> None:
    from app.agent.service import handle_message
    from app.channels.feishu import feishu_channel
    from app.services.user_service import upsert_user

    async with SessionLocal() as session:
        user = await upsert_user(session, "feishu", item["open_id"],
                                 item["open_id"], item["chat_id"])
        reply = await handle_message(session, user, item["text"])
        session.add(MessageLog(user_id=user.id, direction="out", content=reply, status="ok"))
        await session.commit()
        try:
            await feishu_channel.send_text(session, user, reply)
        except Exception:
            import logging
            logging.getLogger("todoagent").exception("reply send failed")
