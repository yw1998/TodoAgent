"""飞书事件回调：验签/解密、去重、快速 ACK、投递队列由后台 worker 消费（单人）。"""

import asyncio
import json
import logging
import time
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request

from app.db.models import MessageLog
from app.db.session import SessionLocal
from app.services import setting_service

logger = logging.getLogger("todoagent.webhook")

router = APIRouter()

queue: asyncio.Queue = asyncio.Queue()
_seen: "OrderedDict[str, float]" = OrderedDict()
_seen_ttl = 300


def _reject(status: int, reason: str, **ctx) -> None:
    logger.warning("reject %s: %s | %s", status, reason,
                   {k: (v[:200] if isinstance(v, str) else v) for k, v in ctx.items()})
    raise HTTPException(status_code=status, detail=reason)


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
        _reject(401, "invalid verification token", got_len=len(token))


async def _learn_notify_target(open_id: str, chat_id: str) -> None:
    """记住最后来消息的会话作为提醒发送目标。"""
    if not open_id:
        return
    async with SessionLocal() as session:
        cfg = await setting_service.get_settings(session)
        updates = {}
        if cfg.get("feishu_notify_open_id") != open_id:
            updates["feishu_notify_open_id"] = open_id
        if chat_id and chat_id.startswith("oc_") and cfg.get("feishu_notify_chat_id") != chat_id:
            updates["feishu_notify_chat_id"] = chat_id
        if updates:
            await setting_service.save_settings(session, updates)
            logger.info("notify target updated: %s", updates.get("feishu_notify_chat_id") or open_id)


@router.post("/feishu/events")
async def feishu_events(request: Request):
    body = await request.body()
    client = request.client.host if request.client else "?"
    logger.info("callback from %s: %d bytes", client, len(body))
    logger.debug("raw body: %s", body[:1000])

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _reject(400, f"invalid JSON body: {e}", body_preview=body[:200].decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        _reject(400, "invalid event payload: not an object", payload_type=type(payload).__name__)

    if "encrypt" in payload:
        async with SessionLocal() as session:
            encrypt_key = await setting_service.get_setting(session, "feishu_encrypt_key")
        if not encrypt_key:
            _reject(400, "payload encrypted but feishu_encrypt_key not configured", keys=list(payload.keys()))
        from app.channels.feishu import decrypt_event
        try:
            payload = decrypt_event(payload["encrypt"], encrypt_key)
        except Exception as e:
            logger.exception("decrypt failed")
            _reject(400, f"decrypt failed: {type(e).__name__} (wrong encrypt_key?)",
                    cipher_len=len(payload["encrypt"]))
        logger.debug("decrypted payload keys: %s", list(payload.keys()))

    if payload.get("type") == "url_verification":
        await _verify_token(payload.get("token", ""))
        logger.info("url_verification ok, challenge echoed")
        return {"challenge": payload.get("challenge", "")}

    header = payload.get("header", {})
    event_type = header.get("event_type", "")
    event_id = header.get("event_id", "")

    if event_type == "card.action.trigger" or payload.get("type") == "action":
        if header:
            await _verify_token(header.get("token", ""))
        from app.api.card_actions import handle_card_action
        resp = await handle_card_action(payload)
        logger.info("card action handled: toast=%r", resp.get("toast", {}).get("content", ""))
        return resp

    if event_type != "im.message.receive_v1":
        logger.info("ignore event %s (type=%s)", event_id or "-", event_type or "-")
        return {"code": 0}
    await _verify_token(header.get("token", ""))

    if event_id and _dedup(event_id):
        logger.info("duplicated event %s, skipped", event_id)
        return {"code": 0, "duplicated": True}

    event = payload.get("event", {})
    msg = event.get("message", {})
    msg_type = msg.get("message_type", "")
    if msg_type not in ("text", "post"):
        logger.info("ignore non-text message %s (type=%s)", event_id or "-", msg_type)
        return {"code": 0}
    try:
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
    except json.JSONDecodeError:
        logger.warning("bad message content json: %s", str(msg.get("content"))[:200])
        text = ""
    open_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    if not text or not open_id:
        logger.warning("drop event %s: empty text(%r) or open_id(%r)",
                       event_id or "-", bool(text), bool(open_id))
        return {"code": 0}

    await _learn_notify_target(open_id, msg.get("chat_id", ""))
    await queue.put({"open_id": open_id, "chat_id": msg.get("chat_id", ""), "text": text})
    logger.info("event %s enqueued: len=%d queue=%d", event_id or "-", len(text), queue.qsize())
    return {"code": 0}  # 快速 ACK，3s 限制


async def worker_loop(concurrency: int) -> None:
    async def worker():
        while True:
            item = await queue.get()
            try:
                await process_message(item)
            except Exception:
                logger.exception("worker error, item=%s", str(item)[:300])
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    logger.info("started %d message workers", concurrency)
    return tasks


async def process_message(item: dict) -> None:
    from app.agent.service import handle_message
    from app.channels.feishu import feishu_channel

    logger.info("processing: text=%r", item["text"][:100])
    async with SessionLocal() as session:
        reply = await handle_message(session, item["text"])
        session.add(MessageLog(direction="out", content=reply, status="ok"))
        await session.commit()
        try:
            await feishu_channel.send_card(session, "📋 TodoAgent", reply)
            logger.info("replied len=%d", len(reply))
        except Exception:
            logger.exception("reply send failed: reply=%r", reply[:200])
