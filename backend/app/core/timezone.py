from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def get_app_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().app_timezone)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return utc_now().astimezone(get_app_tz())


def local_today() -> date:
    return local_now().date()


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local_day_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) for a local calendar date in Asia/Ho_Chi_Minh."""
    tz = get_app_tz()
    start_local = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
    end_local = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, 999999, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
