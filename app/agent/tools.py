"""LLM Tool schema 定义与 handler 注册表（单人使用）。"""

import json
from datetime import datetime

from app.agent.prompts import SYSTEM_PROMPT  # noqa: F401  (service 侧引用)
from app.config import get_env
from app.scheduler import reminder
from app.services import todo_service

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "create_todo",
            "description": "创建一个待办提醒",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待办标题，简短"},
                    "remind_at": {"type": "string", "description": "首次提醒时间，带时区的 ISO8601"},
                    "repeat_cron": {"type": "string", "description": "可选，5 段 cron，如 '0 10 * * 1'"},
                    "note": {"type": "string", "description": "可选备注"},
                },
                "required": ["title", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "查询待办列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pending", "done", "all"],
                               "description": "pending 含逾期(overdue)项，返回结果中 status 字段区分"},
                    "from": {"type": "string", "description": "可选 ISO8601 起始时间"},
                    "to": {"type": "string", "description": "可选 ISO8601 结束时间"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "修改待办的标题/时间/备注/周期，或重新激活",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "remind_at": {"type": "string", "description": "带时区 ISO8601"},
                    "repeat_cron": {"type": "string"},
                    "note": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending"],
                               "description": "传 pending 可把已完成/已取消/逾期的待办重新激活（恢复）"},
                },
                "required": ["todo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "标记待办为已完成",
            "parameters": {
                "type": "object",
                "properties": {"todo_id": {"type": "integer"}},
                "required": ["todo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_todo",
            "description": "取消（删除）待办",
            "parameters": {
                "type": "object",
                "properties": {"todo_id": {"type": "integer"}},
                "required": ["todo_id"],
            },
        },
    },
]


def _fmt(dt_utc) -> str:
    """内部存 UTC naive，展示给 LLM/用户时转成配置时区。"""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    local = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(get_env().scheduler_timezone))
    return local.strftime("%Y-%m-%d %H:%M")


async def h_create(session, args: dict, source_text: str = "") -> dict:
    try:
        remind_at = todo_service.parse_dt(args["remind_at"])
    except (ValueError, KeyError):
        return {"ok": False, "error": f"时间格式无法解析: {args.get('remind_at')}，请向用户确认"}
    if remind_at <= reminder.utcnow():
        return {"ok": False, "error": "提醒时间必须晚于当前时间，请向用户确认时间"}
    todo = await todo_service.create_todo(
        session, title=args["title"], remind_at=remind_at,
        repeat_cron=args.get("repeat_cron") or None, note=args.get("note") or "",
        source=source_text,
    )
    return {"ok": True, "todo_id": todo.id, "title": todo.title,
            "remind_at": _fmt(remind_at), "repeat_cron": todo.repeat_cron}


async def h_list(session, args: dict) -> dict:
    frm = todo_service.parse_dt(args["from"]) if args.get("from") else None
    to = todo_service.parse_dt(args["to"]) if args.get("to") else None
    status = args.get("status", "pending")
    todos = await todo_service.list_todos(session, status, frm, to)
    if status == "pending":
        todos = todos + await todo_service.list_todos(session, "overdue", frm, to)
    return {"ok": True, "todos": [
        {"id": t.id, "title": t.title, "remind_at": _fmt(t.next_remind_at),
         "status": t.status, "repeat_cron": t.repeat_cron} for t in todos]}


async def h_update(session, args: dict) -> dict:
    fields = {k: v for k, v in args.items() if k != "todo_id" and v is not None}
    if fields.get("remind_at"):
        try:
            fields["remind_at"] = todo_service.parse_dt(fields["remind_at"])
        except ValueError:
            return {"ok": False, "error": "时间格式无法解析"}
        if fields["remind_at"] <= reminder.utcnow():
            return {"ok": False, "error": "提醒时间必须晚于当前时间"}
    todo = await todo_service.update_todo(session, int(args["todo_id"]), **fields)
    if not todo:
        return {"ok": False, "error": f"待办 #{args['todo_id']} 不存在"}
    if fields.get("status") == "pending":
        from app import state
        state.clear_last_reminded()
    return {"ok": True, "todo_id": todo.id, "title": todo.title,
            "status": todo.status, "remind_at": _fmt(todo.next_remind_at)}


async def h_complete(session, args: dict) -> dict:
    todo = await todo_service.complete_todo(session, int(args["todo_id"]))
    if not todo:
        return {"ok": False, "error": f"待办 #{args['todo_id']} 不存在"}
    from app import state
    state.clear_if_matches(todo.id)
    return {"ok": True, "todo_id": todo.id, "title": todo.title}


async def h_delete(session, args: dict) -> dict:
    todo_id = int(args["todo_id"])
    ok = await todo_service.delete_todo(session, todo_id)
    if ok:
        from app import state
        state.clear_if_matches(todo_id)
    return {"ok": True} if ok else {"ok": False, "error": "待办不存在"}


TOOL_HANDLERS = {
    "create_todo": h_create,
    "list_todos": h_list,
    "update_todo": h_update,
    "complete_todo": h_complete,
    "delete_todo": h_delete,
}


def build_system_prompt(now_utc: datetime, pending: list[dict], last_reminded_id: int | None = None) -> str:
    from datetime import timezone
    from zoneinfo import ZoneInfo
    env = get_env()
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(env.scheduler_timezone))
    lines = [f"- #{p['id']} {p['title']} (下次提醒 {_fmt(p['remind_at'])}, {p['status']})" for p in pending]
    if last_reminded_id:
        hit = next((p for p in pending if p["id"] == last_reminded_id), None)
        title = f" {hit['title']}" if hit else ""
        lr = f"#{last_reminded_id}{title} —— 30分钟内刚提醒过，裸「完成/延后」指的就是它"
    else:
        lr = "(无。用户说「完成/延后」但未指明编号时，必须追问)"
    return SYSTEM_PROMPT.format(
        now=local.strftime("%Y-%m-%d %H:%M"),
        tz=env.scheduler_timezone,
        pending_todos="\n".join(lines) or "(暂无)",
        last_reminded=lr,
    )
