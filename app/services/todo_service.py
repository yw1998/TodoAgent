"""待办 CRUD 业务逻辑，所有写操作同步维护调度 job。"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Todo, User
from app.scheduler import reminder


def parse_dt(value: str) -> datetime:
    """接受 ISO8601（可带时区）或 YYYY-MM-DD HH:MM，统一转为 naive UTC。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


async def create_todo(session: AsyncSession, user: User, title: str, remind_at: datetime,
                      repeat_cron: str | None = None, note: str = "", source: str = "") -> Todo:
    todo = Todo(user_id=user.id, title=title, note=note or "",
                next_remind_at=remind_at, repeat_cron=repeat_cron, source_message=source)
    session.add(todo)
    await session.commit()
    await session.refresh(todo)
    if todo.next_remind_at > reminder.utcnow():
        reminder.schedule_todo_job(todo.id, todo.next_remind_at)
    return todo


async def get_user_todo(session: AsyncSession, user: User, todo_id: int) -> Todo | None:
    """数据隔离：只允许操作本人待办。"""
    return (await session.execute(
        select(Todo).where(Todo.id == todo_id, Todo.user_id == user.id)
    )).scalar_one_or_none()


async def list_todos(session: AsyncSession, user: User, status: str = "pending",
                     frm: datetime | None = None, to: datetime | None = None) -> list[Todo]:
    q = select(Todo).where(Todo.user_id == user.id)
    if status in ("pending", "done", "cancelled"):
        q = q.where(Todo.status == status)
    elif status != "all":
        q = q.where(Todo.status != "cancelled")
    if frm:
        q = q.where(Todo.next_remind_at >= frm)
    if to:
        q = q.where(Todo.next_remind_at <= to)
    return list((await session.execute(q.order_by(Todo.next_remind_at))).scalars().all())


async def update_todo(session: AsyncSession, user: User, todo_id: int, **fields) -> Todo | None:
    todo = await get_user_todo(session, user, todo_id)
    if not todo or todo.status != "pending":
        return None
    changed_time = False
    if fields.get("title") is not None:
        todo.title = str(fields["title"])[:255]
    if fields.get("note") is not None:
        todo.note = str(fields["note"])
    if fields.get("remind_at") is not None:
        todo.next_remind_at = fields["remind_at"] if isinstance(fields["remind_at"], datetime) else parse_dt(str(fields["remind_at"]))
        changed_time = True
    if fields.get("repeat_cron") is not None:
        todo.repeat_cron = fields["repeat_cron"] or None
        changed_time = True
    await session.commit()
    if changed_time:
        reminder.schedule_todo_job(todo.id, todo.next_remind_at)
    return todo


async def complete_todo(session: AsyncSession, user: User, todo_id: int) -> Todo | None:
    todo = await get_user_todo(session, user, todo_id)
    if not todo:
        return None
    todo.status = "done"
    todo.done_at = reminder.utcnow()
    await session.commit()
    reminder.cancel_todo_job(todo.id)
    return todo


async def delete_todo(session: AsyncSession, user: User, todo_id: int) -> bool:
    todo = await get_user_todo(session, user, todo_id)
    if not todo:
        return False
    todo.status = "cancelled"
    await session.commit()
    reminder.cancel_todo_job(todo.id)
    return True


async def admin_query(session: AsyncSession, status: str | None = None, user_id: int | None = None,
                      q: str | None = None, frm: datetime | None = None, to: datetime | None = None,
                      page: int = 1, size: int = 20):
    sq = select(Todo)
    if status:
        sq = sq.where(Todo.status == status)
    if user_id:
        sq = sq.where(Todo.user_id == user_id)
    if q:
        sq = sq.where(or_(Todo.title.contains(q), Todo.note.contains(q)))
    if frm:
        sq = sq.where(Todo.created_at >= frm)
    if to:
        sq = sq.where(Todo.created_at <= to)
    rows = (await session.execute(
        sq.order_by(Todo.next_remind_at).offset((page - 1) * size).limit(size)
    )).scalars().all()
    return rows
