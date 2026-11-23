from dataclasses import dataclass
from datetime import date, timedelta

from app.core.config import get_settings
from app.core.timezone import local_today
from app.integrations.portal.exceptions import PortalIntegrationError


@dataclass(frozen=True)
class SemesterRange:
    start_date: date
    end_date: date
    week_anchors: list[date]


def _parse_optional_iso_date(raw: str | None, field_name: str) -> date | None:
    if raw is None or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise PortalIntegrationError(
            f"Invalid ISO date format for {field_name} (expected YYYY-MM-DD).",
            error_code="SEMESTER_RANGE_NOT_CONFIGURED",
        ) from exc


def resolve_semester_range(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    today: date | None = None,
    configured_start: str | None = None,
    configured_end: str | None = None,
    max_weeks: int | None = None,
) -> SemesterRange:
    """
    Resolve the exact [start_date, end_date] and Monday week anchors for semester sync.

    Rules:
      - Never assumes a hardcoded semester length (e.g., 16 weeks).
      - Missing end date (both explicit and PORTAL_SEMESTER_END_DATE) raises
        PortalIntegrationError with error_code='SEMESTER_RANGE_NOT_CONFIGURED'.
      - Defaults start_date to Monday of the current week (or configured_start if later).
      - Caps total weeks at `max_weeks` to prevent accidental unbounded Portal queries.
    """
    settings = get_settings()
    ref_today = today or local_today()
    current_week_monday = ref_today - timedelta(days=ref_today.weekday())

    raw_start = (
        configured_start if configured_start is not None else settings.portal_semester_start_date
    )
    raw_end = (
        configured_end if configured_end is not None else settings.portal_semester_end_date
    )
    cap_weeks = max_weeks if max_weeks is not None else settings.portal_semester_max_weeks

    resolved_end = end_date or _parse_optional_iso_date(raw_end, "PORTAL_SEMESTER_END_DATE")
    if resolved_end is None:
        raise PortalIntegrationError(
            "Portal semester end date is not configured. Set PORTAL_SEMESTER_END_DATE (YYYY-MM-DD).",
            error_code="SEMESTER_RANGE_NOT_CONFIGURED",
        )

    parsed_cfg_start = _parse_optional_iso_date(raw_start, "PORTAL_SEMESTER_START_DATE")
    if start_date is not None:
        resolved_start = start_date
    elif parsed_cfg_start is not None and parsed_cfg_start > current_week_monday:
        resolved_start = parsed_cfg_start
    else:
        resolved_start = current_week_monday

    if resolved_end < resolved_start:
        raise PortalIntegrationError(
            "Semester end date is earlier than the sync start date.",
            error_code="INVALID_SEMESTER_RANGE",
        )

    first_monday = resolved_start - timedelta(days=resolved_start.weekday())
    last_monday = resolved_end - timedelta(days=resolved_end.weekday())

    anchors: list[date] = []
    cursor = first_monday
    while cursor <= last_monday:
        anchors.append(cursor)
        cursor += timedelta(days=7)

    if len(anchors) > cap_weeks:
        raise PortalIntegrationError(
            f"Configured semester range spans {len(anchors)} weeks, exceeding max allowed ({cap_weeks}).",
            error_code="SEMESTER_RANGE_TOO_LARGE",
        )

    return SemesterRange(
        start_date=resolved_start,
        end_date=resolved_end,
        week_anchors=anchors,
    )
