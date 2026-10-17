from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Todo, User


async def find_user(session: AsyncSession, channel: str, external_id: str) -> User | None:
    if not external_id:
        return None
    return (await session.execute(
        select(User).where(User.channel == channel, User.external_id == external_id)
    )).scalar_one_or_none()


async def upsert_user(session: AsyncSession, channel: str, external_id: str,
                      name: str = "", chat_id: str = "") -> User:
    user = (await session.execute(
        select(User).where(User.channel == channel, User.external_id == external_id)
    )).scalar_one_or_none()
    if user:
        changed = False
        if name and user.name != name:
            user.name, changed = name, True
        if chat_id and user.chat_id != chat_id:
            user.chat_id, changed = chat_id, True
        if changed:
            await session.commit()
        return user
    user = User(channel=channel, external_id=external_id, name=name or "", chat_id=chat_id or "")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_users_with_counts(session: AsyncSession) -> list[tuple[User, int]]:
    rows = (await session.execute(
        select(User, func.count(Todo.id).filter(Todo.status == "pending"))
        .outerjoin(Todo, Todo.user_id == User.id)
        .group_by(User.id)
        .order_by(User.id)
    )).all()
    return [(u, c) for u, c in rows]
