import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.database.session import get_session_factory
from app.integrations.telegram.client import TelegramBotClient
from app.notifications.service import NotificationService
from app.scheduler.jobs import (
    run_assignment_reminders,
    run_class_reminders,
    run_custom_reminders,
    run_daily_summary,
    run_portal_semester_sync,
    run_weekly_summary,
)
from app.scheduler.locks import RedisDistributedLock

logger = logging.getLogger(__name__)


def _execute_locked(lock_name: str, job_fn) -> None:  # type: ignore[no-untyped-def]
    lock = RedisDistributedLock()
    with lock.acquire(lock_name) as acquired:
        if not acquired:
            logger.debug("Skipping %s — lock held by another worker.", lock_name)
            return
        db = get_session_factory()()
        try:
            notif_service = NotificationService(db=db, telegram_client=TelegramBotClient())
            sent = job_fn(db, notif_service)
            logger.info("Scheduled job=%s completed sent=%d", lock_name, sent)
        except Exception as exc:
            logger.exception("Scheduled job=%s failed: %s", lock_name, type(exc).__name__)
        finally:
            db.close()


def create_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(
        timezone=settings.app_timezone,
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    scheduler.add_job(
        lambda: _execute_locked("class_reminders", run_class_reminders),
        trigger=IntervalTrigger(minutes=5),
        id="class_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _execute_locked("assignment_reminders", run_assignment_reminders),
        trigger=IntervalTrigger(minutes=30),
        id="assignment_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _execute_locked("custom_reminders", run_custom_reminders),
        trigger=IntervalTrigger(minutes=1),
        id="custom_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _execute_locked("daily_summary", run_daily_summary),
        trigger=CronTrigger(hour=7, minute=0, timezone=settings.app_timezone),
        id="daily_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _execute_locked("weekly_summary", run_weekly_summary),
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=settings.app_timezone),
        id="weekly_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _execute_locked("portal_semester_sync", run_portal_semester_sync),
        trigger=IntervalTrigger(hours=settings.portal_auto_sync_interval_hours),
        id="portal_semester_sync",
        replace_existing=True,
    )
    return scheduler
