"""飞书渠道：tenant_access_token 缓存、发消息、事件验签与解密。"""

import base64
import hashlib
import json
import time

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.channels.base import Channel
from app.db.models import User
from app.services import setting_service

BASE_URL = "https://open.feishu.cn"


class FeishuChannel(Channel):
    name = "feishu"

    def __init__(self):
        self._token: str = ""
        self._token_expire_at: float = 0

    async def _get_token(self, session) -> str:
        if self._token and time.time() < self._token_expire_at - 300:
            return self._token
        cfg = await setting_service.get_settings(session)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": cfg.get("feishu_app_id", ""), "app_secret": cfg.get("feishu_app_secret", "")},
            )
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"feishu token error: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + data.get("expire", 7200)
        return self._token

    async def send_text(self, session, user: User, text: str) -> None:
        token = await self._get_token(session)
        receive_id_type = "chat_id" if user.chat_id.startswith("oc_") else "open_id"
        receive_id = user.chat_id or user.external_id
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
                headers={"Authorization": f"Bearer {token}"},
                json={"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": text})},
            )
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"feishu send error: {data}")


def verify_signature(timestamp: str, nonce: str, body: bytes, signature: str, encrypt_key: str) -> bool:
    if not encrypt_key:
        return True
    expected = hashlib.sha256(f"{timestamp}{nonce}{encrypt_key}{body.decode('utf-8')}".encode("utf-8")).hexdigest()
    return expected == signature


def decrypt_event(encrypted_b64: str, encrypt_key: str) -> dict:
    """飞书 AES-256-CBC: key=sha256(encrypt_key), iv=前16字节密文。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypted_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(raw[:16]))
    dec = cipher.decryptor()
    padded = dec.update(raw[16:]) + dec.finalize()
    return json.loads(padded[: -padded[-1]].decode("utf-8"))


feishu_channel = FeishuChannel()
