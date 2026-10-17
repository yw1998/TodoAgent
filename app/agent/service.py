"""AgentService：组装上下文 → LLM tool calling 循环 → 生成回复（单人）。"""

import json
import logging
import time
from collections import deque

from sqlalchemy.ext.asyncio import AsyncSession

from app import state
from app.agent import tools
from app.config import get_env
from app.db.models import MessageLog
from app.llm import client as llm_client
from app.scheduler.reminder import utcnow
from app.services import todo_service

MAX_TOOL_ROUNDS = 4
logger = logging.getLogger("todoagent.agent")
_context: deque = deque(maxlen=get_env().context_rounds * 2)


async def handle_message(session: AsyncSession, text: str) -> str:
    pending = await todo_service.list_todos(session, "pending")
    pending += await todo_service.list_todos(session, "overdue")
    pending_summary = [{"id": t.id, "title": t.title, "remind_at": t.next_remind_at,
                        "status": t.status} for t in pending[:50]]
    messages = [
        {"role": "system", "content": tools.build_system_prompt(
            utcnow(), pending_summary, state.get_last_reminded())},
        *_context,
        {"role": "user", "content": text},
    ]
    tool_trace: list[dict] = []
    start = time.monotonic()
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await llm_client.chat(session, messages, tools.TOOL_SCHEMAS)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                reply = msg.content or "抱歉，我没有理解你的意思，能换个说法吗？"
                _append_context(text, reply)
                await _log(session, "in", text, tool_trace, "ok", "", start)
                return reply
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                handler = tools.TOOL_HANDLERS.get(tc.function.name)
                if not handler:
                    result = {"ok": False, "error": "unknown tool"}
                elif tc.function.name == "create_todo":
                    result = await handler(session, args, source_text=text)
                else:
                    result = await handler(session, args)
                tool_trace.append({"tool": tc.function.name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        reply = "抱歉，我处理这条消息时绕了太多弯路，请换个说法试试。"
        _append_context(text, reply)
        await _log(session, "in", text, tool_trace, "error", "max tool rounds exceeded", start)
        return reply
    except Exception as e:
        logger.exception("agent failed: text=%r", text[:100])
        reply = f"抱歉，系统处理出错了（{type(e).__name__}），请稍后重试。"
        await _log(session, "in", text, tool_trace, "error", str(e)[:500], start)
        return reply


def _append_context(text: str, reply: str) -> None:
    _context.append({"role": "user", "content": text})
    _context.append({"role": "assistant", "content": reply})


async def _log(session, direction, content, tool_trace, status, error, start) -> None:
    session.add(MessageLog(
        direction=direction, content=content,
        tool_calls=tool_trace or None, status=status, error=error,
        latency_ms=int((time.monotonic() - start) * 1000),
    ))
    await session.commit()
