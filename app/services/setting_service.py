"""配置服务：DB settings 表优先，env 兜底；secret 项 AES-GCM 加密存储，热生效。"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_env
from app.db.models import Setting

SECRET_KEYS = {"llm_api_key", "feishu_app_secret", "feishu_verification_token", "feishu_encrypt_key"}

KNOWN_KEYS = [
    "llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout",
    "feishu_app_id", "feishu_app_secret", "feishu_verification_token", "feishu_encrypt_key",
    "feishu_notify_open_id", "feishu_notify_chat_id",
    "scheduler_timezone", "scheduler_tick_seconds", "scheduler_max_retry",
    "reminder_max_times", "reminder_interval_minutes",
]

_cache: dict[str, str] | None = None


def _fernet_key() -> AESGCM:
    dk = hashlib.sha256(get_env().secret_key.encode()).digest()
    return AESGCM(dk)


def encrypt(value: str) -> str:
    nonce = os.urandom(12)
    ct = _fernet_key().encrypt(nonce, value.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(value: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(value)
        return _fernet_key().decrypt(raw[:12], raw[12:], None).decode()
    except Exception:
        import logging
        logging.getLogger("todoagent.settings").error(
            "secret 解密失败：SECRET_KEY 与保存配置时不一致，该项将回退到环境变量默认值")
        return ""


async def load_cache(session: AsyncSession) -> dict[str, str]:
    global _cache
    rows = (await session.execute(_select_all())).scalars().all()
    _cache = {r.key: (decrypt(r.value) if r.is_secret else r.value) for r in rows}
    return _cache


def _select_all():
    from sqlalchemy import select
    return select(Setting)


def invalidate() -> None:
    global _cache
    _cache = None


async def get_settings(session: AsyncSession) -> dict[str, str]:
    if _cache is None:
        await load_cache(session)
    env = get_env().model_dump()
    merged = {k: str(v) for k, v in env.items() if k in KNOWN_KEYS}
    merged.update(_cache or {})
    return merged


async def get_setting(session: AsyncSession, key: str) -> str:
    return (await get_settings(session)).get(key, "")


async def save_settings(session: AsyncSession, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in KNOWN_KEYS or value == "***":
            continue
        is_secret = key in SECRET_KEYS
        row = await session.get(Setting, key)
        stored = encrypt(value) if is_secret else value
        if row:
            row.value = stored
        else:
            session.add(Setting(key=key, value=stored, is_secret=is_secret))
    await session.commit()
    invalidate()


async def get_settings_for_display(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(_select_all())).scalars().all()
    return [
        {
            "key": r.key,
            "value": "***" if r.is_secret and r.value else "",
            "is_secret": r.is_secret,
            "set": bool(r.value) if r.is_secret else True,
        }
        for r in rows
    ]
