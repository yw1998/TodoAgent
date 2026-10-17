"""提醒调度：APScheduler jobstore + 周期性 DB 兜底扫描，乐观锁防重复发送。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone as tz
from zoneinfo import ZoneInfo

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import get_env
from app.db.models import ReminderLog, Todo
from app.db.session import SessionLocal

_env = get_env()
logger = logging.getLogger("todoagent.scheduler")
_scheduler: AsyncIOScheduler | None = None


def utcnow() -> datetime:
    return datetime.now(tz.utc).replace(tzinfo=None)


def _to_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=tz.utc)


def fmt_local(dt_utc: datetime) -> str:
    local = _to_aware(dt_utc).astimezone(ZoneInfo(_env.scheduler_timezone))
    return f"{local:%Y-%m-%d %H:%M} ({_env.scheduler_timezone})"


def next_cron_time(cron: str, base_utc: datetime) -> datetime:
    tzn = ZoneInfo(_env.scheduler_timezone)
    trigger = CronTrigger.from_crontab(cron, timezone=tzn)
    nxt = trigger.get_next_fire_time(None, _to_aware(base_utc).astimezone(tzn))
    return nxt.astimezone(tz.utc).replace(tzinfo=None) if nxt else base_utc + timedelta(days=1)


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        sync_url = _env.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")
        jobstores = {"default": SQLAlchemyJobStore(url=sync_url) if ":memory:" not in sync_url else None}
        if jobstores["default"] is None:
            from apscheduler.jobstores.memory import MemoryJobStore
            jobstores["default"] = MemoryJobStore()
        _scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors={"default": AsyncIOExecutor()},
            timezone="UTC",
        )
    return _scheduler


def schedule_todo_job(todo_id: int, when_utc: datetime) -> None:
    if not (_scheduler and _scheduler.running):
        return  # 服务未启动时不注册，启动时由 restore_jobs 统一恢复
    sched = get_scheduler()
    sched.add_job(
        fire_reminder, args=[todo_id],
        trigger=DateTrigger(run_date=_to_aware(when_utc)),
        id=f"todo-{todo_id}", replace_existing=True,
    )


def cancel_todo_job(todo_id: int) -> None:
    try:
        get_scheduler().remove_job(f"todo-{todo_id}")
    except Exception:
        pass


async def _load_todo(session, todo_id: int) -> Todo | None:
    return (await session.execute(
        select(Todo).options(selectinload(Todo.user)).where(Todo.id == todo_id)
    )).scalar_one_or_none()


async def fire_reminder(todo_id: int) -> None:
    async with SessionLocal() as session:
        todo = await _load_todo(session, todo_id)
        if not todo or todo.status != "pending":
            return
        now = utcnow()
        if todo.next_remind_at > now:  # 已被修改/延后，重新注册即可
            schedule_todo_job(todo.id, todo.next_remind_at)
            return
        claimed = await session.execute(
            update(Todo)
            .where(Todo.id == todo.id,
                   Todo.remind_count == todo.remind_count,
                   Todo.next_remind_at <= now)
            .values(remind_count=Todo.remind_count + 1)
        )
        if claimed.rowcount == 0:
            logger.info("reminder todo#%s claimed by others, skip", todo_id)
            return  # 其他执行者已抢占
        await session.commit()
        todo = (await session.execute(
            select(Todo).options(selectinload(Todo.user)).where(Todo.id == todo_id),
            execution_options={"populate_existing": True},
        )).scalar_one()  # 重新加载，拿到自增后的 remind_count
        logger.info("firing reminder todo#%s title=%r count=%d", todo_id, todo.title, todo.remind_count)

        from app.channels.feishu import build_card, reminder_buttons
        from app.services import setting_service
        cfg = await setting_service.get_settings(session)
        max_times = int(cfg.get("reminder_max_times", 3) or 3)
        interval = int(cfg.get("reminder_interval_minutes", 5) or 5)
        md = (f"**{todo.title}**\n"
              f"提醒时间：{fmt_local(todo.next_remind_at)}")
        if not todo.repeat_cron and max_times > 1:
            md += f"\n第 {todo.remind_count}/{max_times} 次提醒"
            if todo.remind_count >= max_times:
                md += "，之后将标记为逾期"
        card = build_card("⏰ 待办提醒", md, "orange",
                          [reminder_buttons(todo.id),
                           {"tag": "note", "elements": [{"tag": "plain_text",
                             "content": "也可以直接回复「完成」或「延后10分钟」"}]}])
        await _send_with_retry(session, todo, card)

        todo = await _load_todo(session, todo_id)
        if todo.repeat_cron:
            todo.next_remind_at = next_cron_time(todo.repeat_cron, utcnow())
            await session.commit()
            schedule_todo_job(todo.id, todo.next_remind_at)
        elif todo.remind_count >= max_times:
            todo.status = "overdue"
            await session.commit()
            cancel_todo_job(todo.id)
            from app import state as app_state
            app_state.clear_if_matches(todo.user_id, todo.id)
            logger.info("todo#%s overdue after %d reminders (max=%d)", todo.id, todo.remind_count, max_times)
        else:
            todo.next_remind_at = utcnow() + timedelta(minutes=interval)
            await session.commit()
            schedule_todo_job(todo.id, todo.next_remind_at)


async def _send_with_retry(session, todo: Todo, card: dict) -> None:
    from app.channels.feishu import feishu_channel
    from app import state

    max_retry = int(_env.scheduler_max_retry)
    delay = 2
    for attempt in range(max_retry + 1):
        try:
            await feishu_channel.send_card_message(session, todo.user, card)
            state.set_last_reminded(todo.user_id, todo.id)
            logger.info("reminder sent todo#%s, last_reminded=%d->%d",
                        todo.id, todo.user_id, todo.id)
            session.add(ReminderLog(todo_id=todo.id, status="ok"))
            await session.commit()
            return
        except Exception as e:
            logger.warning("send reminder todo#%s attempt %d/%d failed: %s",
                           todo.id, attempt + 1, max_retry + 1, e)
            if attempt == max_retry:
                logger.error("reminder todo#%s permanently failed: %s", todo.id, e)
                session.add(ReminderLog(todo_id=todo.id, status="error", error=str(e)[:500]))
                await session.commit()
                return
            await asyncio.sleep(delay)
            delay *= 4


async def tick() -> None:
    """兜底扫描：处理 job 丢失或服务停机期间错过的提醒。"""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Todo.id).where(Todo.status == "pending", Todo.next_remind_at <= utcnow())
        )).scalars().all()
    for todo_id in rows:
        await fire_reminder(todo_id)


async def restore_jobs() -> None:
    """启动时把所有 pending 待办重新注册为 job。"""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Todo).options(selectinload(Todo.user)).where(Todo.status == "pending")
        )).scalars().all()
    for todo in rows:
        if todo.next_remind_at <= utcnow():
            asyncio.get_running_loop().create_task(fire_reminder(todo.id))
        else:
            schedule_todo_job(todo.id, todo.next_remind_at)


def start() -> None:
    sched = get_scheduler()
    sched.add_job(tick, "interval", seconds=int(_env.scheduler_tick_seconds),
                  id="fallback-tick", replace_existing=True)
    sched.start()


def shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
