"""管理后台 REST API（单人使用）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import create_token
from app.db.models import Admin, MessageLog, Todo
from app.db.session import get_session, verify_password
from app.scheduler import reminder
from app.services import setting_service, todo_service

router = APIRouter(prefix="/api/admin")


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody, session: AsyncSession = Depends(get_session)):
    admin = (await session.execute(select(Admin).where(Admin.username == body.username))).scalar_one_or_none()
    if not admin or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": create_token(admin)}


@router.get("/todos")
async def list_todos(status: str | None = None, q: str | None = None,
                     page: int = 1, size: int = 20,
                     session: AsyncSession = Depends(get_session)):
    rows, total = await todo_service.admin_query(session, status, q, page=page, size=size)
    return {"total": total, "page": page, "size": size, "items": [{
        "id": t.id, "title": t.title, "note": t.note,
        "next_remind_at": t.next_remind_at.isoformat(), "repeat_cron": t.repeat_cron,
        "status": t.status, "remind_count": t.remind_count,
        "created_at": t.created_at.isoformat(),
    } for t in rows]}


@router.get("/todos/{todo_id}")
async def todo_detail(todo_id: int, session: AsyncSession = Depends(get_session)):
    todo = (await session.execute(
        select(Todo).options(selectinload(Todo.reminder_logs)).where(Todo.id == todo_id)
    )).scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=404)
    return {
        "id": todo.id, "title": todo.title, "note": todo.note, "status": todo.status,
        "next_remind_at": todo.next_remind_at.isoformat(), "repeat_cron": todo.repeat_cron,
        "source_message": todo.source_message, "remind_count": todo.remind_count,
        "created_at": todo.created_at.isoformat(),
        "reminder_logs": [{"fired_at": r.fired_at.isoformat(), "status": r.status, "error": r.error}
                          for r in todo.reminder_logs],
    }


class TodoPatch(BaseModel):
    title: str | None = None
    note: str | None = None
    remind_at: datetime | None = None
    status: str | None = None


@router.patch("/todos/{todo_id}")
async def patch_todo(todo_id: int, body: TodoPatch, session: AsyncSession = Depends(get_session)):
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    todo = await todo_service.update_todo(session, todo_id, **fields) if fields else None
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在或状态不允许修改")
    return {"ok": True, "status": todo.status}


@router.delete("/todos/{todo_id}")
async def cancel_todo(todo_id: int, session: AsyncSession = Depends(get_session)):
    if not await todo_service.delete_todo(session, todo_id):
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)):
    return await setting_service.get_settings_for_display(session)


class SettingsBody(BaseModel):
    values: dict[str, str]


@router.put("/settings")
async def put_settings(body: SettingsBody, session: AsyncSession = Depends(get_session)):
    await setting_service.save_settings(session, body.values)
    return {"ok": True}


@router.get("/message-logs")
async def message_logs(status: str | None = None, direction: str | None = None,
                       q: str | None = None, page: int = 1, size: int = 50,
                       session: AsyncSession = Depends(get_session)):
    sq = select(MessageLog).order_by(MessageLog.id.desc()).offset((page - 1) * size).limit(size)
    if status:
        sq = sq.where(MessageLog.status == status)
    if direction:
        sq = sq.where(MessageLog.direction == direction)
    if q:
        sq = sq.where(MessageLog.content.contains(q))
    rows = (await session.execute(sq)).scalars().all()
    return [{"id": m.id, "direction": m.direction, "content": m.content,
             "tool_calls": m.tool_calls, "status": m.status, "error": m.error,
             "latency_ms": m.latency_ms, "created_at": m.created_at.isoformat()} for m in rows]
