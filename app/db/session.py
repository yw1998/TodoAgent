import hashlib
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_env
from app.db.models import Admin, Base

_env = get_env()

_sync_url = _env.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")
if _sync_url.startswith("sqlite:///"):
    db_path = _sync_url.split("sqlite:///")[1]
    if db_path and db_path != ":memory:":
        Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(_env.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
        return dk.hex() == dk_hex
    except ValueError:
        return False


async def init_db() -> None:
    from app.db import models  # noqa: F401  ensure all tables registered

    async with engine.begin() as conn:
        if _sync_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        from sqlalchemy import select

        exists = await session.scalar(select(Admin).limit(1))
        if not exists:
            session.add(Admin(
                username=_env.admin_init_username,
                password_hash=hash_password(_env.admin_init_password),
            ))
            await session.commit()


async def get_session() -> AsyncSession:  # FastAPI 依赖
    async with SessionLocal() as session:
        yield session
