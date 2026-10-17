"""飞书渠道（单人使用）：tenant_access_token 缓存、向固定通知目标发消息、事件验签与解密。"""

import base64
import hashlib
import json
import re
import time

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services import setting_service

BASE_URL = "https://open.feishu.cn"


class FeishuChannel:
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

    async def _send(self, session, msg_type: str, content: dict) -> None:
        cfg = await setting_service.get_settings(session)
        chat_id = cfg.get("feishu_notify_chat_id", "")
        open_id = cfg.get("feishu_notify_open_id", "")
        receive_id_type, receive_id = ("chat_id", chat_id) if chat_id else ("open_id", open_id)
        if not receive_id:
            raise RuntimeError("通知目标未设置：先给机器人发一条消息，或在配置页填写 feishu_notify_*")
        token = await self._get_token(session)
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

    async def send_text(self, session, text: str) -> None:
        await self._send(session, "text", {"text": text})

    async def send_card(self, session, title: str, markdown: str,
                        template: str = "blue", extra_elements: list[dict] | None = None) -> None:
        await self._send(session, "interactive",
                         build_card(title, markdown, template, extra_elements))

    async def send_card_message(self, session, card: dict) -> None:
        await self._send(session, "interactive", card)


def build_card(title: str, markdown: str, template: str = "blue",
               extra_elements: list[dict] | None = None) -> dict:
    """飞书卡片：markdown 元素渲染 **加粗** 等语法；表格语法自动转原生 table 组件。"""
    return {
        "config": {"wide_title_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": _card_elements(markdown) + (extra_elements or []),
    }


def reminder_buttons(todo_id: int, snooze_minutes: int = 30) -> dict:
    return {
        "tag": "action",
        "actions": [
            {"tag": "button",
             "text": {"tag": "plain_text", "content": "✅ 完成"},
             "type": "primary", "action_type": "request",
             "value": {"act": "complete", "todo_id": str(todo_id)}},
            {"tag": "button",
             "text": {"tag": "plain_text", "content": f"⏰ 延后{snooze_minutes}分钟"},
             "type": "default", "action_type": "request",
             "value": {"act": "snooze", "todo_id": str(todo_id),
                       "minutes": str(snooze_minutes)}},
        ],
    }


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


_FENCE_RE = re.compile(r"^\s*```[^\n]*$")


def _normalize_markdown(md: str) -> str:
    """飞书卡片 markdown 不支持反引号代码语法(实测原样显示)：
    行内 `x` → *x* 斜体；围栏代码块 → 紫色彩色文本行。"""
    out, fence = [], None
    for line in md.splitlines():
        is_fence = bool(_FENCE_RE.match(line))
        if is_fence and fence is None:
            fence = []
            continue
        if is_fence and fence is not None:
            for l in fence:
                if l.strip():
                    out.append(f"<font color='purple'>{l}</font>" if "<" not in l else l)
            fence = None
            continue
        if fence is not None:
            fence.append(line)
        else:
            out.append(line)
    if fence:
        out.extend(fence)
    md = "\n".join(out)

    def _inline(m):
        s = m.group(1).strip() or m.group(1)
        return f"*{s}*" if "*" not in s and "<" not in s else f"<font color='purple'>{s}</font>"
    md = re.sub(r"``([^`\n]+?)``", _inline, md)
    md = re.sub(r"`([^`\n]+?)`", _inline, md)
    return md


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
