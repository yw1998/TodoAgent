from datetime import timedelta

from app.channels.feishu import FeishuChannel
from app.scheduler.reminder import fire_reminder, utcnow
from app.services import todo_service

sent_cards: list[dict] = []


def _fake_send(monkeypatch):
    sent_cards.clear()

    async def fake(self, session, card):
        sent_cards.append(card)
    monkeypatch.setattr(FeishuChannel, "send_card_message", fake)


async def _fire_at(session, todo):
    todo.next_remind_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    await fire_reminder(todo.id)
    await session.refresh(todo)


async def test_repeat_reminder_then_overdue(session, monkeypatch):
    _fake_send(monkeypatch)
    todo = await todo_service.create_todo(session, "测试政策", utcnow() + timedelta(hours=1))
    import json

    await _fire_at(session, todo)  # 第1次
    assert todo.remind_count == 1 and todo.status == "pending"
    assert todo.next_remind_at > utcnow()
    assert "第 1/3 次提醒" in json.dumps(sent_cards[-1], ensure_ascii=False)

    await _fire_at(session, todo)  # 第2次
    assert todo.remind_count == 2 and todo.status == "pending"
    assert "第 2/3 次提醒" in json.dumps(sent_cards[-1], ensure_ascii=False)

    await _fire_at(session, todo)  # 第3次 = 最大次数 → 逾期（不是完成）
    assert todo.remind_count == 3
    assert todo.status == "overdue" and todo.done_at is None
    assert "标记为逾期" in json.dumps(sent_cards[-1], ensure_ascii=False)


async def test_overdue_snooze_reactivates(session, monkeypatch):
    _fake_send(monkeypatch)
    todo = await todo_service.create_todo(session, "逾期测试", utcnow() + timedelta(hours=1))
    for _ in range(3):
        await _fire_at(session, todo)
    assert todo.status == "overdue"

    from app.api.card_actions import handle_card_action
    resp = await handle_card_action({
        "header": {"event_type": "card.action.trigger", "token": ""},
        "event": {"operator": {"open_id": "ou_od"},
                  "action": {"value": {"act": "snooze", "todo_id": str(todo.id), "minutes": "30"}}}})
    assert resp["toast"]["type"] == "success"
    await session.refresh(todo)
    assert todo.status == "pending" and todo.remind_count == 0


async def test_repeat_cron_uses_cron_not_interval(session, monkeypatch):
    _fake_send(monkeypatch)
    todo = await todo_service.create_todo(session, "喝水", utcnow() + timedelta(hours=1),
                                          repeat_cron="0 10 * * *")
    await _fire_at(session, todo)
    assert todo.remind_count == 1 and todo.status == "pending"
    assert todo.next_remind_at.hour == 2  # 下一次 cron 10:00 CST = 02:00 UTC
