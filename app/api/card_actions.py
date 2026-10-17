"""卡片按钮回调：同步执行 完成/延后 并返回新卡片（单人使用，无需归属校验）。

兼容两种回调格式：
- 旧版 interactive 卡片: {"type":"action","content":"{...}"}
- 事件订阅 v2.0: header.event_type == card.action.trigger
"""

import json
import logging
from datetime import timedelta

from app.channels.feishu import build_card
from app.db.session import SessionLocal
from app.scheduler.reminder import fmt_local, utcnow
from app.services import todo_service
from app import state

logger = logging.getLogger("todoagent.card")


def extract_action(payload: dict) -> dict | None:
    """返回 action_value，非卡片动作返回 None。"""
    if payload.get("type") == "action" and "content" in payload:
        try:
            content = json.loads(payload["content"])
        except (json.JSONDecodeError, TypeError):
            return None
        value = (content.get("action") or {}).get("value") or {}
    else:
        event = payload.get("event") or {}
        value = (event.get("action") or {}).get("value") or {}
    if not value.get("act"):
        return None
    return value


def _resp(card: dict | None, toast_type: str, toast_text: str) -> dict:
    out = {"toast": {"type": toast_type, "content": toast_text}}
    if card is not None:
        out["card"] = {"type": "raw", "content": json.dumps(card, ensure_ascii=False)}
    return out


async def handle_card_action(payload: dict) -> dict:
    value = extract_action(payload)
    if value is None:
        return _resp(None, "info", "无法识别的操作")
    act, todo_id = value.get("act"), value.get("todo_id")
    if not (todo_id or "").isdigit():
        return _resp(None, "error", "无效的操作对象")

    async with SessionLocal() as session:
        todo = await todo_service.get_todo(session, int(todo_id))
        if not todo:
            return _resp(None, "error", "待办不存在")

        if act == "complete":
            if todo.status in ("done", "cancelled"):
                card = build_card(f"ℹ️ 待办提醒 · {todo.title}", f"**{todo.title}**\n当前状态：{todo.status}，无需重复操作", "grey")
                return _resp(card, "info", "该待办已处理")
            await todo_service.complete_todo(session, todo.id)
            state.clear_if_matches(todo.id)
            _log(session, f"🔘 点击按钮完成 #{todo.id} {todo.title}")
            logger.info("button complete todo#%s", todo.id)
            card = build_card(f"✅ 已完成 · {todo.title}", f"**{todo.title}**\n完成时间：{fmt_local(utcnow())}", "green")
            return _resp(card, "success", "已标记完成")

        if act == "snooze":
            minutes = value.get("minutes", "30")
            minutes = int(minutes) if str(minutes).isdigit() else 30
            if todo.status in ("done", "cancelled"):
                card = build_card(f"ℹ️ 待办提醒 · {todo.title}", f"**{todo.title}**\n当前状态：{todo.status}，无法延后", "grey")
                return _resp(card, "info", "该待办已处理")
            new_at = utcnow() + timedelta(minutes=minutes)
            if todo.status == "overdue":
                await todo_service.update_todo(session, todo.id, remind_at=new_at, status="pending")
            else:
                await todo_service.update_todo(session, todo.id, remind_at=new_at)
            state.set_last_reminded(todo.id)
            _log(session, f"🔘 点击按钮延后 #{todo.id} {todo.title} {minutes}分钟")
            logger.info("button snooze todo#%s +%dmin", todo.id, minutes)
            card = build_card(f"⏰ 已延后 · {todo.title}",
                              f"**{todo.title}**\n将在 {fmt_local(new_at)} 再次提醒", "orange")
            return _resp(card, "success", f"已延后 {minutes} 分钟")

        return _resp(None, "error", "未知操作")


def _log(session, text: str) -> None:
    from app.db.models import MessageLog
    session.add(MessageLog(direction="in", content=text, status="ok"))
    session.commit()
