import json
from datetime import timedelta
from types import SimpleNamespace

from app.agent.service import handle_message, _context
from app.llm import client as llm_client
from app.scheduler.reminder import utcnow


class FakeToolCall:
    def __init__(self, cid, name, args):
        self.id = cid
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args))

    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


def make_resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


async def test_agent_create_todo(session, monkeypatch):
    _context.clear()
    when = (utcnow() + timedelta(hours=20)).isoformat()
    calls = [
        make_resp(tool_calls=[FakeToolCall("c1", "create_todo",
                                           {"title": "买菜", "remind_at": when})]),
        make_resp(content="已为你创建待办「买菜」"),
    ]

    async def fake_chat(sess, messages, tools):
        return calls.pop(0)

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    reply = await handle_message(session, "明天早上10点提醒我买菜")
    assert "买菜" in reply

    from app.services import todo_service
    todos = await todo_service.list_todos(session, "pending")
    assert len(todos) == 1 and todos[0].title == "买菜"


async def test_agent_error_path(session, monkeypatch):
    async def boom(sess, messages, tools):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_client, "chat", boom)
    reply = await handle_message(session, "随便说点什么")
    assert "出错" in reply
