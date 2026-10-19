from app.services import setting_service


async def test_save_hot_visible(session):
    setting_service.invalidate()
    await setting_service.save_settings(session, {"scheduler_tick_seconds": "10"})
    assert setting_service.peek("scheduler_tick_seconds") == "10"
    assert (await setting_service.get_setting(session, "scheduler_tick_seconds")) == "10"
    display = {r["key"]: r["value"] for r in await setting_service.get_settings_for_display(session)}
    assert display["scheduler_tick_seconds"] == "10"


async def test_peek_falls_back_to_env(session):
    setting_service.invalidate()
    assert setting_service.peek("scheduler_timezone") == "Asia/Shanghai"
    await setting_service.save_settings(session, {"scheduler_timezone": "UTC"})
    assert setting_service.peek("scheduler_timezone") == "UTC"
    # 清理，避免影响其他用例
    from app.db.models import Setting
    row = await session.get(Setting, "scheduler_timezone")
    await session.delete(row)
    await session.commit()
    setting_service.invalidate()


async def test_apply_runtime_noop_when_stopped():
    from app.scheduler.reminder import apply_runtime
    apply_runtime()  # 调度器未启动时静默返回，不抛异常
