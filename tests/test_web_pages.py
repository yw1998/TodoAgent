from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.api.auth import create_token
from app.db.models import Admin
from app.main import app
from app.scheduler.reminder import utcnow
from app.services import todo_service, user_service


@pytest.fixture
async def client(session):
    admin = (await session.execute(select(Admin).limit(1))).scalar_one()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 cookies={"token": create_token(admin)}) as c:
        yield c


async def test_todos_page_shows_local_time(client, session):
    user = await user_service.upsert_user(session, "feishu", "ou_page")
    when = utcnow() + timedelta(days=1)
    when = when.replace(hour=2, minute=0, second=0, microsecond=0)  # 02:00 UTC = 10:00 上海
    await todo_service.create_todo(session, user, "页面时区测试", when)

    resp = await client.get("/admin/todos")
    assert resp.status_code == 200
    assert "页面时区测试" in resp.text
    expected = datetime(when.year, when.month, when.day, 10, 0).strftime("%Y-%m-%d %H:%M")
    assert expected in resp.text
    assert "(UTC)" not in resp.text


async def test_pages_render(client):
    for path in ("/admin/todos", "/admin/users", "/admin/settings", "/admin/logs"):
        resp = await client.get(path)
        assert resp.status_code == 200, path
    assert "时区 Asia/Shanghai" in (await client.get("/admin/todos")).text


async def test_logs_page_filters(client, session):
    from app.db.models import MessageLog

    user = await user_service.upsert_user(session, "feishu", "ou_log", "日志君", "oc_1")
    session.add(MessageLog(user_id=user.id, direction="in", content="明天记得带伞",
                           tool_calls=[{"tool": "create_todo", "args": {"title": "带伞"},
                                        "result": {"ok": True, "todo_id": 42}}],
                           status="ok", latency_ms=1234))
    session.add(MessageLog(user_id=user.id, direction="out", content="已创建 ✅", status="ok"))
    session.add(MessageLog(user_id=user.id, direction="in", content="boom", status="error",
                           error="llm down"))
    await session.commit()

    page = (await client.get("/admin/logs")).text
    assert "明天记得带伞" in page and "create_todo" in page and "日志君" in page

    only_in = (await client.get("/admin/logs?direction=in")).text
    assert "明天记得带伞" in only_in and "已创建" not in only_in

    only_err = (await client.get("/admin/logs?status=error")).text
    assert "boom" in only_err and "llm down" in only_err

    searched = (await client.get("/admin/logs?q=带伞")).text
    assert "明天记得带伞" in searched and "boom" not in searched
