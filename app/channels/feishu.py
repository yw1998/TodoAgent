"""飞书渠道：tenant_access_token 缓存、发消息、事件验签与解密。"""

import base64
import hashlib
import json
import re
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

    async def _send(self, session, user: User, msg_type: str, content: dict) -> None:
        token = await self._get_token(session)
        receive_id_type = "chat_id" if user.chat_id.startswith("oc_") else "open_id"
        receive_id = user.chat_id or user.external_id
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
                headers={"Authorization": f"Bearer {token}"},
                json={"receive_id": receive_id, "msg_type": msg_type,
                      "content": json.dumps(content, ensure_ascii=False)},
            )
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"feishu send error: {data}")

    async def send_text(self, session, user: User, text: str) -> None:
        await self._send(session, user, "text", {"text": text})

    async def send_card(self, session, user: User, title: str, markdown: str,
                        template: str = "blue") -> None:
        await self._send(session, user, "interactive", build_card(title, markdown, template))


def build_card(title: str, markdown: str, template: str = "blue") -> dict:
    """飞书卡片：markdown 元素渲染 **加粗** 等语法；表格语法自动转原生 table 组件。"""
    return {
        "config": {"wide_title_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": _card_elements(markdown),
    }


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


_FENCE_RE = re.compile(r"^\s*```[^\n]*$")


def _normalize_markdown(md: str) -> str:
    """飞书卡片 markdown 不支持围栏代码块与 `` 双反引号，转换为可行形式。"""
    md = re.sub(r"``([^`\n]+?)``", r"`\1`", md)
    out, fence = [], None
    for line in md.splitlines():
        is_fence = bool(_FENCE_RE.match(line))
        if is_fence and fence is None:
            fence = []
            continue
        if is_fence and fence is not None:
            for l in fence:
                if not l.strip():
                    continue
                out.append(f"`{l}`" if "`" not in l and len(l) <= 80 else l)
            fence = None
            continue
        if fence is not None:
            fence.append(line)
        else:
            out.append(line)
    if fence:
        out.extend(fence)
    return "\n".join(out)


def _is_table_sep(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in s.split("|") if c.strip() != "") \
        and "-" in s


def _card_elements(markdown: str) -> list[dict]:
    """把 markdown 文本按表格分段：表格→原生 table，其余→markdown。"""
    lines = _normalize_markdown(markdown).splitlines()
    elements, buf, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            if buf:
                elements.append({"tag": "markdown", "content": "\n".join(buf).strip()})
                buf = []
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _split_row(lines[i])
                rows.append({f"c{j}": (cells[j] if j < len(cells) else "") for j in range(len(header))})
                i += 1
            elements.append({
                "tag": "table",
                "page_size": max(len(rows), 1),
                "row_height": "low",
                "header_info": True,
                "columns": [{"name": f"c{j}", "display_name": h or "-", "width": "auto"}
                            for j, h in enumerate(header)],
                "rows": rows,
            })
            continue
        buf.append(line)
        i += 1
    if buf:
        elements.append({"tag": "markdown", "content": "\n".join(buf).strip()})
    return elements or [{"tag": "markdown", "content": markdown}]


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
