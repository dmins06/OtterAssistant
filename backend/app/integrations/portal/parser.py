from dataclasses import dataclass
from datetime import date, datetime, time
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.integrations.portal.exceptions import PortalIntegrationError
from app.models.enums import SessionPeriod

# Matches real Portal UTH room strings from portal.ut.edu.vn/calendar, e.g.:
#   "Phòng: C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)"
#   "Phòng: Hội Trường Hội Nhập CS1 - (P.Thạnh Mỹ Tây, TP.HCM)"
_ROOM_PATTERN = re.compile(
    r"^(?:Phòng\s*:\s*)?(?P<room>.+?)\s*(?:-\s*)?(?P<campus>CS\d+)\s*(?:-\s*)?(?:\((?P<location>[^)]+)\))?$",
    re.IGNORECASE,
)

# Sensitive query parameters that MUST NEVER be persisted or returned in LMS URLs
_SENSITIVE_URL_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "wstoken",
        "jwt",
        "auth",
        "key",
        "api_key",
        "secret",
        "password",
        "session",
        "sesskey",
        "ticket",
        "code",
    }
)


@dataclass(frozen=True)
class ParsedRoomInfo:
    room: str | None
    campus: str | None
    location: str | None
    raw_room_text: str | None


@dataclass(frozen=True)
class PortalScheduleItem:
    source_event_id: str
    subject_code: str
    subject_name: str
    class_date: date
    weekday: int
    session: SessionPeriod
    class_session: str
    period_start: int
    period_end: int
    start_time: time
    end_time: time
    room: str | None = None
    campus: str | None = None
    location: str | None = None
    raw_room_text: str | None = None
    syllabus_url: str | None = None
    lms_url: str | None = None
    course_section_code: str | None = None
    lecturer: str | None = None
    is_cancelled: bool = False
    note: str | None = None
    location_type: str | None = None
    time_display: str | None = None
    have_outline_file: bool | None = None
    have_book: bool | None = None
    outline_description: str | None = None


def parse_uth_room_text(raw_text: str | None) -> ParsedRoomInfo:
    """Parse UTH Portal room string into structured (room, campus, location)."""
    if not raw_text or not raw_text.strip():
        return ParsedRoomInfo(room=None, campus=None, location=None, raw_room_text=None)

    cleaned = raw_text.strip()
    match = _ROOM_PATTERN.match(cleaned)
    if not match:
        room_only = re.sub(r"^Phòng\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
        return ParsedRoomInfo(room=room_only or None, campus=None, location=None, raw_room_text=cleaned)

    room = match.group("room").strip(" -") or None
    campus = match.group("campus").upper().strip() if match.group("campus") else None
    location = match.group("location").strip() if match.group("location") else None
    return ParsedRoomInfo(room=room, campus=campus, location=location, raw_room_text=cleaned)


def sanitize_lms_url(raw_url: str | None) -> str | None:
    """
    Remove token and other sensitive query parameters from Portal-provided LMS links
    before persistence or exposure. Keeps only safe http(s) scheme, host, path, and
    non-sensitive query parameters.
    """
    if not raw_url or not isinstance(raw_url, str):
        return None
    cleaned = raw_url.strip()
    if not cleaned:
        return None

    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None

    safe_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.strip().lower() not in _SENSITIVE_URL_QUERY_KEYS
    ]
    safe_query = urlencode(safe_params, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))


def derive_uth_session_and_ca(period_start: int, start_time: time) -> tuple[SessionPeriod, str]:
    """
    Map UTH starting period (and fallback start_time) to official UTH Buổi (Sáng/Chiều/Tối)
    and Ca học (Ca 1..Ca 5).
    """
    if 1 <= period_start <= 3:
        return SessionPeriod.MORNING, "Ca 1"
    if 4 <= period_start <= 6:
        return SessionPeriod.MORNING, "Ca 2"
    if 7 <= period_start <= 9:
        return SessionPeriod.AFTERNOON, "Ca 3"
    if 10 <= period_start <= 12:
        return SessionPeriod.AFTERNOON, "Ca 4"
    if period_start >= 13:
        return SessionPeriod.EVENING, "Ca 5"

    if start_time < time(12, 0):
        return SessionPeriod.MORNING, "Ca 1"
    if start_time < time(17, 30):
        return SessionPeriod.AFTERNOON, "Ca 3"
    return SessionPeriod.EVENING, "Ca 5"


def reconcile_weekday(class_date: date, raw_thu: Any = None) -> int:
    """
    Compute canonical Vietnamese weekday from `class_date` (Monday=2 .. Saturday=7, Sunday=8).
    Using `class_date.weekday() + 2` guarantees correctness regardless of whether Portal's `thu`
    uses 2..8 (Vietnamese), 1..7 (Sun=1), or 0..6.
    """
    _ = raw_thu  # Verified against class_date rather than blindly assuming Portal's numbering
    return class_date.weekday() + 2


def _parse_portal_date(raw_value: Any, field_name: str) -> date:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise PortalIntegrationError(
            f"Missing or invalid required date field '{field_name}'.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )
    try:
        return datetime.strptime(raw_value.strip(), "%d/%m/%Y").date()
    except ValueError as exc:
        raise PortalIntegrationError(
            f"Invalid date format for '{field_name}' (expected dd/MM/yyyy).",
            error_code="PORTAL_INVALID_PAYLOAD",
        ) from exc


def _parse_portal_time(raw_value: Any, field_name: str) -> time:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise PortalIntegrationError(
            f"Missing or invalid required time field '{field_name}'.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )
    cleaned = raw_value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    raise PortalIntegrationError(
        f"Invalid time format for '{field_name}' (expected HH:mm).",
        error_code="PORTAL_INVALID_PAYLOAD",
    )


def _require_int(entry: dict[str, Any], field_name: str) -> int:
    val = entry.get(field_name)
    if val is None or isinstance(val, bool):
        raise PortalIntegrationError(
            f"Missing required integer field '{field_name}'.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )
    try:
        return int(val)
    except (TypeError, ValueError) as exc:
        raise PortalIntegrationError(
            f"Invalid integer value for field '{field_name}'.",
            error_code="PORTAL_INVALID_PAYLOAD",
        ) from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def parse_portal_lich_tuan_event(entry: Any) -> PortalScheduleItem:
    """Parse a single element from `body` of GET /api/v1/lichhoc/lichTuan."""
    if not isinstance(entry, dict):
        raise PortalIntegrationError(
            "Portal schedule event entry must be a JSON object.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )

    raw_id = entry.get("id")
    if raw_id is None or str(raw_id).strip() == "":
        raise PortalIntegrationError(
            "Missing required field 'id' in Portal schedule event.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )
    source_event_id = str(raw_id).strip()

    subject_code = _optional_str(entry.get("maMonHoc")) or _optional_str(entry.get("maLopHocPhan"))
    subject_name = _optional_str(entry.get("tenMonHoc"))
    if not subject_code or not subject_name:
        raise PortalIntegrationError(
            "Missing required subject fields ('maMonHoc'/'tenMonHoc') in Portal schedule event.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )

    class_date = _parse_portal_date(entry.get("ngayBatDauHoc"), "ngayBatDauHoc")
    start_time = _parse_portal_time(entry.get("tuGio"), "tuGio")
    end_time = _parse_portal_time(entry.get("denGio"), "denGio")
    if end_time <= start_time:
        raise PortalIntegrationError(
            "Invalid schedule time range: denGio must be after tuGio.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )

    period_start = _require_int(entry, "tuTiet")
    period_end = _require_int(entry, "denTiet")
    if period_start < 1 or period_end < period_start:
        raise PortalIntegrationError(
            "Invalid period range: denTiet must be >= tuTiet >= 1.",
            error_code="PORTAL_INVALID_PAYLOAD",
        )

    weekday = reconcile_weekday(class_date, entry.get("thu"))
    session, class_session = derive_uth_session_and_ca(period_start, start_time)

    raw_room = _optional_str(entry.get("tenPhong"))
    room_info = parse_uth_room_text(raw_room)
    campus = _optional_str(entry.get("coSoToDisplay")) or room_info.campus
    room = room_info.room or raw_room

    sanitized_lms = sanitize_lms_url(entry.get("link"))

    return PortalScheduleItem(
        source_event_id=source_event_id,
        subject_code=subject_code,
        subject_name=subject_name,
        class_date=class_date,
        weekday=weekday,
        session=session,
        class_session=class_session,
        period_start=period_start,
        period_end=period_end,
        start_time=start_time,
        end_time=end_time,
        room=room,
        campus=campus,
        location=room_info.location,
        raw_room_text=raw_room,
        lms_url=sanitized_lms,
        course_section_code=_optional_str(entry.get("maLopHocPhan")),
        lecturer=_optional_str(entry.get("giangVien")),
        is_cancelled=bool(entry.get("isTamNgung", False)),
        note=_optional_str(entry.get("ghiChu")),
        location_type=_optional_str(entry.get("image")),
        time_display=_optional_str(entry.get("timeToDisplay")),
        have_outline_file=_optional_bool(entry.get("haveOutlineFile")),
        have_book=_optional_bool(entry.get("haveBook")),
        outline_description=_optional_str(entry.get("outLineDescription")),
    )


def parse_portal_lich_tuan_response(payload: Any) -> list[PortalScheduleItem]:
    """Validate and parse the full JSON envelope returned by /api/v1/lichhoc/lichTuan."""
    if not isinstance(payload, dict):
        raise PortalIntegrationError(
            "Portal UTH response must be a JSON object.",
            error_code="PORTAL_UNEXPECTED_RESPONSE",
        )

    success = payload.get("success")
    if success is False:
        raw_msg = _optional_str(payload.get("message")) or "Portal UTH returned success=false."
        raise PortalIntegrationError(raw_msg, error_code="PORTAL_API_ERROR")
    if success is not True:
        raise PortalIntegrationError(
            "Portal UTH response missing boolean 'success' flag.",
            error_code="PORTAL_UNEXPECTED_RESPONSE",
        )

    body = payload.get("body")
    if body is None:
        return []
    if not isinstance(body, list):
        raise PortalIntegrationError(
            "Portal UTH response 'body' must be a JSON array.",
            error_code="PORTAL_UNEXPECTED_RESPONSE",
        )

    return [parse_portal_lich_tuan_event(entry) for entry in body]
