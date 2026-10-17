from datetime import timedelta

from app.channels.feishu import FeishuChannel
from app.scheduler.reminder import fire_reminder, utcnow
from app.services import todo_service, user_service


sent_cards: list[dict] = []


def _fake_send(monkeypatch):
    sent_cards.clear()

    async def fake(self, session, user, card):
        sent_cards.append(card)
    monkeypatch.setattr(FeishuChannel, "send_card_message", fake)


async def _fire_at(session, todo):
    todo.next_remind_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    await fire_reminder(todo.id)
    await session.refresh(todo)


async def test_repeat_reminder_then_auto_done(session, monkeypatch):
    _fake_send(monkeypatch)
    user = await user_service.upsert_user(session, "feishu", "ou_pol", "P", "oc_1")
    todo = await todo_service.create_todo(session, user, "测试政策", utcnow() + timedelta(hours=1))

    await _fire_at(session, todo)  # 第1次
    assert todo.remind_count == 1 and todo.status == "pending"
    assert todo.next_remind_at > utcnow()  # 间隔 5 分钟后再提醒
    import json
    assert "第 1/3 次提醒" in json.dumps(sent_cards[-1], ensure_ascii=False)

    await _fire_at(session, todo)  # 第2次
    assert todo.remind_count == 2 and todo.status == "pending"
    assert "第 2/3 次提醒" in json.dumps(sent_cards[-1], ensure_ascii=False)

    await _fire_at(session, todo)  # 第3次 = 最大次数 → 自动完成
    assert todo.remind_count == 3
    assert todo.status == "done" and todo.done_at is not None


async def test_repeat_cron_uses_cron_not_interval(session, monkeypatch):
    _fake_send(monkeypatch)
    user = await user_service.upsert_user(session, "feishu", "ou_cron", "C", "oc_1")
    todo = await todo_service.create_todo(session, user, "喝水", utcnow() + timedelta(hours=1),
                                          repeat_cron="0 10 * * *")
    await _fire_at(session, todo)
    assert todo.remind_count == 1 and todo.status == "pending"
    assert todo.next_remind_at.hour == 2  # 下一次 cron 10:00 CST = 02:00 UTC


async def test_snooze_still_respects_max(session, monkeypatch):
    _fake_send(monkeypatch)
    user = await user_service.upsert_user(session, "feishu", "ou_snz", "S", "oc_1")
    todo = await todo_service.create_todo(session, user, "取件", utcnow() + timedelta(hours=1))
    await _fire_at(session, todo)
    assert todo.remind_count == 1
    await todo_service.update_todo(session, user, todo.id, remind_at=utcnow() + timedelta(minutes=30))
    await _fire_at(session, todo)
    assert todo.remind_count == 2 and todo.status == "pending"
