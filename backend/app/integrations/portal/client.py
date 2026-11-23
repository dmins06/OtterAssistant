from datetime import date, time, timedelta
import logging
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.integrations.portal.exceptions import PortalHTTPStatusError, PortalIntegrationError
from app.integrations.portal.parser import (
    PortalScheduleItem,
    parse_portal_lich_tuan_response,
    parse_uth_room_text,
)
from app.integrations.portal.token_manager import PortalTokenBundle
from app.models.enums import SessionPeriod

logger = logging.getLogger(__name__)

CONFIRMED_LICH_TUAN_ENDPOINT = "/api/v1/lichhoc/lichTuan"


class PortalProvider(Protocol):
    """Interface for Portal UTH clients (real HTTP or mock)."""

    is_mock: bool
    supports_refresh: bool

    def refresh_access_token(self, refresh_token: str) -> PortalTokenBundle:
        ...

    def fetch_weekly_schedule(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        csrf_token: str | None = None,
    ) -> list[PortalScheduleItem]:
        ...


class UTHPortalHttpClient:
    """
    Real HTTPS client for UTH Student Portal (`https://portal.ut.edu.vn`).
    Uses confirmed endpoint: GET /api/v1/lichhoc/lichTuan?date=YYYY-MM-DD
    Never logs Authorization/Cookie headers, raw JWTs, or unsanitized LMS links.
    """

    is_mock: bool = False

    def __init__(
        self,
        base_url: str | None = None,
        schedule_endpoint: str | None = None,
        refresh_endpoint: str | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.portal_base_url).rstrip("/")
        configured_sched = (
            schedule_endpoint if schedule_endpoint is not None else settings.portal_schedule_endpoint
        ).strip()
        self.schedule_endpoint = configured_sched or CONFIRMED_LICH_TUAN_ENDPOINT
        self.refresh_endpoint = (
            refresh_endpoint if refresh_endpoint is not None else settings.portal_refresh_endpoint
        ).strip()
        self.timeout = httpx.Timeout(connect=5.0, read=timeout_seconds, write=10.0, pool=5.0)
        self._transport = transport

    @property
    def supports_refresh(self) -> bool:
        return bool(self.refresh_endpoint)

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        code = response.status_code
        if 200 <= code < 300:
            return

        retry_after: float | None = None
        if "Retry-After" in response.headers:
            try:
                retry_after = float(response.headers["Retry-After"])
            except ValueError:
                retry_after = None

        body_preview = response.text[:200].lower() if response.text else ""
        is_csrf = code == 403 and ("csrf" in body_preview or "xsrf" in body_preview)
        raise PortalHTTPStatusError(
            status_code=code,
            message=f"Portal UTH returned HTTP {code}",
            error_code="PORTAL_CSRF_ERROR" if is_csrf else f"PORTAL_HTTP_{code}",
            retry_after_seconds=retry_after,
            is_csrf_error=is_csrf,
        )

    def refresh_access_token(self, refresh_token: str) -> PortalTokenBundle:
        if not self.refresh_endpoint:
            raise PortalIntegrationError(
                "Portal refresh endpoint is not configured/verified.",
                error_code="REFRESH_NOT_SUPPORTED",
            )
        url = self._build_url(self.refresh_endpoint)
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(url, json={"refresh_token": refresh_token})
                self._raise_for_status(response)
                data: dict[str, Any] = response.json()
        except PortalIntegrationError:
            raise
        except httpx.TimeoutException as exc:
            raise PortalIntegrationError(
                "Portal UTH refresh request timed out.",
                error_code="PORTAL_TIMEOUT",
            ) from exc
        except httpx.RequestError as exc:
            raise PortalIntegrationError(
                "Network error while refreshing Portal UTH token.",
                error_code="PORTAL_NETWORK_ERROR",
            ) from exc
        except ValueError as exc:
            raise PortalIntegrationError(
                "Portal UTH refresh returned malformed JSON.",
                error_code="PORTAL_INVALID_JSON",
            ) from exc

        access = data.get("access_token") or data.get("token")
        if not access:
            raise PortalIntegrationError(
                "Portal refresh response did not contain an access token.",
                error_code="INVALID_REFRESH_RESPONSE",
            )
        return PortalTokenBundle(
            access_token=str(access),
            refresh_token=str(data["refresh_token"]) if data.get("refresh_token") else refresh_token,
            csrf_token=str(data["csrf_token"]) if data.get("csrf_token") else None,
        )

    def fetch_schedule_for_week(
        self,
        access_token: str,
        anchor_date: date,
        csrf_token: str | None = None,
    ) -> list[PortalScheduleItem]:
        """Fetch the weekly schedule containing `anchor_date` via GET /api/v1/lichhoc/lichTuan?date=YYYY-MM-DD."""
        url = self._build_url(self.schedule_endpoint)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token

        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.get(
                    url,
                    headers=headers,
                    params={"date": anchor_date.isoformat()},
                )
                self._raise_for_status(response)
                payload = response.json()
        except PortalIntegrationError:
            raise
        except httpx.TimeoutException as exc:
            logger.warning("Portal UTH lichTuan request timed out for date=%s", anchor_date.isoformat())
            raise PortalIntegrationError(
                "Portal UTH schedule request timed out.",
                error_code="PORTAL_TIMEOUT",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Portal UTH lichTuan network error for date=%s: %s",
                anchor_date.isoformat(),
                type(exc).__name__,
            )
            raise PortalIntegrationError(
                "Network error communicating with Portal UTH.",
                error_code="PORTAL_NETWORK_ERROR",
            ) from exc
        except ValueError as exc:
            logger.warning("Portal UTH lichTuan returned malformed JSON for date=%s", anchor_date.isoformat())
            raise PortalIntegrationError(
                "Portal UTH returned invalid JSON response.",
                error_code="PORTAL_INVALID_JSON",
            ) from exc

        items = parse_portal_lich_tuan_response(payload)
        logger.info(
            "Portal UTH lichTuan fetched date=%s events=%d",
            anchor_date.isoformat(),
            len(items),
        )
        return items

    def fetch_weekly_schedule(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        csrf_token: str | None = None,
    ) -> list[PortalScheduleItem]:
        """
        Query /api/v1/lichhoc/lichTuan once per distinct week spanning [start_date, end_date]
        (avoiding per-day requests) and deduplicate events by `source_event_id`.
        """
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        # Compute Monday anchors covering [start_date, end_date]
        first_monday = start_date - timedelta(days=start_date.weekday())
        last_monday = end_date - timedelta(days=end_date.weekday())
        anchors: list[date] = []
        cursor = first_monday
        while cursor <= last_monday:
            anchors.append(cursor)
            cursor += timedelta(days=7)

        deduped: dict[str, PortalScheduleItem] = {}
        for anchor in anchors:
            week_items = self.fetch_schedule_for_week(
                access_token=access_token,
                anchor_date=anchor,
                csrf_token=csrf_token,
            )
            for item in week_items:
                deduped[item.source_event_id] = item

        return list(deduped.values())


class MockPortalClient:
    """
    Explicit mock Portal client for offline development and automated tests.
    Uses the exact UTH Portal timetable structure verified from the user's screenshot.
    """

    is_mock: bool = True

    def __init__(
        self,
        items: list[PortalScheduleItem] | None = None,
        supports_refresh: bool = False,
    ) -> None:
        self.supports_refresh = supports_refresh
        self._items = items

    def refresh_access_token(self, refresh_token: str) -> PortalTokenBundle:
        if not self.supports_refresh:
            raise PortalIntegrationError(
                "Mock portal client does not support refresh.",
                error_code="REFRESH_NOT_SUPPORTED",
            )
        return PortalTokenBundle(
            access_token=f"refreshed-{refresh_token}",
            refresh_token=f"rotated-{refresh_token}",
        )

    def fetch_weekly_schedule(
        self,
        access_token: str,  # noqa: ARG002
        start_date: date,
        end_date: date,
        csrf_token: str | None = None,  # noqa: ARG002
    ) -> list[PortalScheduleItem]:
        if self._items is not None:
            return [i for i in self._items if start_date <= i.class_date <= end_date]

        room_c007 = parse_uth_room_text("Phòng: C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)")
        room_hall = parse_uth_room_text("Phòng: Hội Trường Hội Nhập CS1 - (P.Thạnh Mỹ Tây, TP.HCM)")
        sample = [
            PortalScheduleItem(
                source_event_id="mock-uth-2026-12-07-012000510605-ca3",
                subject_code="012000510605",
                subject_name="Kinh tế chính trị Mác - Lênin",
                class_date=date(2026, 12, 7),
                weekday=2,
                session=SessionPeriod.AFTERNOON,
                class_session="Ca 3",
                period_start=7,
                period_end=9,
                start_time=time(12, 10),
                end_time=time(14, 40),
                room=room_hall.room,
                campus=room_hall.campus,
                location=room_hall.location,
                raw_room_text=room_hall.raw_room_text,
                syllabus_url="https://portal.ut.edu.vn/syllabus/012000510605",
                lms_url="https://courses.ut.edu.vn/course/view.php?id=605",
            ),
            PortalScheduleItem(
                source_event_id="mock-uth-2026-12-12-012012710505-ca2",
                subject_code="012012710505",
                subject_name="Học sâu",
                class_date=date(2026, 12, 12),
                weekday=7,
                session=SessionPeriod.MORNING,
                class_session="Ca 2",
                period_start=4,
                period_end=6,
                start_time=time(9, 25),
                end_time=time(11, 55),
                room=room_c007.room,
                campus=room_c007.campus,
                location=room_c007.location,
                raw_room_text=room_c007.raw_room_text,
                syllabus_url="https://portal.ut.edu.vn/syllabus/012012710505",
                lms_url="https://courses.ut.edu.vn/course/view.php?id=505",
            ),
        ]
        return [i for i in sample if start_date <= i.class_date <= end_date]
