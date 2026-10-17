import hashlib
import logging
import os
from datetime import datetime, timezone
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


_MIGRATE_TABLES = {
    "todos": ["id", "title", "note", "next_remind_at", "repeat_cron", "status",
              "source_message", "remind_count", "created_at", "updated_at", "done_at"],
    "message_logs": ["id", "direction", "content", "tool_calls", "status",
                     "error", "latency_ms", "created_at"],
}


def _carry_notify_target(sync_conn) -> None:
    """迁移前把 users 表最后一条记录的会话信息转存为通知目标配置。"""
    row = sync_conn.exec_driver_sql(
        "SELECT external_id, chat_id FROM users ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return
    open_id, chat_id = row[0] or "", row[1] or ""

    def _set(key, val):
        if not val:
            return
        cur = sync_conn.exec_driver_sql(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if cur is None or not cur[0]:
            if cur is None:
                sync_conn.exec_driver_sql(
                    "INSERT INTO settings (key, value, is_secret, updated_at) VALUES (?, ?, 0, ?)",
                    (key, val, utcnow_str()))
            else:
                sync_conn.exec_driver_sql("UPDATE settings SET value=? WHERE key=?", (val, key))

    _set("feishu_notify_open_id", open_id)
    _set("feishu_notify_chat_id", chat_id)


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _migrate_drop_users(conn) -> None:
    """单人部署：删除历史 users 表及 user_id 列（重建表搬数据，保留全部记录）。"""
    if conn.engine.dialect.name != "sqlite":
        return

    def run(sync_conn):
        from sqlalchemy import inspect
        existing = set(inspect(sync_conn).get_table_names())
        for table, cols in _MIGRATE_TABLES.items():
            if table not in existing:
                continue
            colnames = [r[1] for r in sync_conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
            if "user_id" not in colnames:
                continue
            new_name = f"{table}_new"
            from sqlalchemy import MetaData
            tmp_md = MetaData()
            tbl = Base.metadata.tables[table].to_metadata(tmp_md, name=new_name)
            for ix in list(tbl.indexes):
                tbl.indexes.remove(ix)
            tbl.create(sync_conn, checkfirst=False)
            shared = [c for c in cols if c in colnames]
            cols_sql = ", ".join(shared)
            sync_conn.exec_driver_sql(f"INSERT INTO {new_name} ({cols_sql}) SELECT {cols_sql} FROM {table}")
            sync_conn.exec_driver_sql(f"DROP TABLE {table}")
            sync_conn.exec_driver_sql(f"ALTER TABLE {new_name} RENAME TO {table}")
            for ix in Base.metadata.tables[table].indexes:
                ix.create(sync_conn, checkfirst=True)
        if "users" in existing and "settings" in existing:
            _carry_notify_target(sync_conn)
        sync_conn.exec_driver_sql("DROP TABLE IF EXISTS users")
        for (name,) in sync_conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall():
            idx_cols = [r[2] for r in sync_conn.exec_driver_sql(f"PRAGMA index_info('{name}')").fetchall()]
            if "user_id" in idx_cols:
                sync_conn.exec_driver_sql(f"DROP INDEX IF EXISTS {name}")

    await conn.run_sync(run)


async def init_db() -> None:
    from app.db import models  # noqa: F401  ensure all tables registered

    async with engine.begin() as conn:
        if _sync_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await _migrate_drop_users(conn)
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
