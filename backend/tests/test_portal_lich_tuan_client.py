from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from typing import Any

from cryptography.fernet import Fernet
import httpx
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import SensitiveDataFilter, redact_sensitive
from app.core.security import SecretEncryptor
from app.integrations.portal.client import UTHPortalHttpClient
from app.integrations.portal.exceptions import (
    PortalHTTPStatusError,
    PortalIntegrationError,
    PortalReauthRequiredError,
)
from app.integrations.portal.parser import (
    parse_portal_lich_tuan_response,
    sanitize_lms_url,
)
from app.integrations.portal.schedule_sync import PortalScheduleSyncService
from app.integrations.portal.session_manager import PortalSessionManager
from app.integrations.portal.token_manager import PortalTokenManager
from app.models.academic import Schedule, Subject
from app.models.enums import ConnectionStatus, SessionPeriod, SyncStatus
from app.models.user import User


def _make_jwt(exp_delta_seconds: int = 3600) -> str:
    exp = int((datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)).timestamp())
    return jwt.encode({"sub": "2251120001", "exp": exp}, "sanitized-test-secret", algorithm="HS256")


def _make_token_manager() -> PortalTokenManager:
    return PortalTokenManager(SecretEncryptor(key=Fernet.generate_key().decode("utf-8")))


def _sample_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 900101,
        "ngayBatDauHoc": "23/11/2026",
        "tenPhong": "C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
        "thu": 2,
        "tuTiet": 4,
        "denTiet": 6,
        "maLopHocPhan": "012012710505",
        "maMonHoc": "0120127105",
        "tenMonHoc": "Học sâu & Thị giác máy tính",
        "giangVien": "TS. Nguyễn Văn A",
        "isTamNgung": False,
        "tuGio": "09:25",
        "denGio": "11:55",
        "link": "https://courses.ut.edu.vn/course/view.php?id=505&token=SUPER_SECRET_LMS_TOKEN_XYZ&lang=vi",
        "ghiChu": "Mang theo laptop",
        "image": "CS1_ROOM",
        "coSoToDisplay": "CS1",
        "timeToDisplay": "09:25 - 11:55",
        "haveOutlineFile": True,
        "haveBook": False,
        "outLineDescription": "Đề cương chi tiết môn Học sâu",
        "idMonHocNCT": None,
        "nameToDisplay": None,
        "dateToDisplay": None,
        "text1": None,
        "text2": None,
        "text3": None,
    }
    base.update(overrides)
    return base


def _envelope(body: Any, success: bool = True, status: int = 200, message: str | None = None) -> dict[str, Any]:
    return {
        "success": success,
        "status": status,
        "message": message,
        "body": body,
        "token": None,
        "timestamp": "2026-11-23T04:00:00Z",
    }


def test_real_client_successful_multiple_events_vietnamese_dates_times_and_weekday() -> None:
    captured_requests: list[httpx.Request] = []
    fake_jwt = _make_jwt(3600)

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json=_envelope(
                [
                    _sample_event(
                        id=101,
                        ngayBatDauHoc="23/11/2026",  # Monday
                        thu=2,
                        tenMonHoc="Kinh tế chính trị Mác - Lênin",
                        tuTiet=7,
                        denTiet=9,
                        tuGio="12:10",
                        denGio="14:40",
                    ),
                    _sample_event(
                        id=102,
                        ngayBatDauHoc="29/11/2026",  # Sunday -> canonical weekday=8 even if thu=1
                        thu=1,
                        tenMonHoc="Trực quan hóa dữ liệu",
                        tuTiet=4,
                        denTiet=6,
                        tuGio="09:25",
                        denGio="11:55",
                        giangVien=None,
                        ghiChu=None,
                        image=None,
                        timeToDisplay=None,
                        haveOutlineFile=None,
                        haveBook=None,
                        outLineDescription=None,
                    ),
                ]
            ),
        )

    client = UTHPortalHttpClient(
        base_url="https://portal.ut.edu.vn",
        schedule_endpoint="/api/v1/lichhoc/lichTuan",
        transport=httpx.MockTransport(handler),
    )

    # Single week range (Mon 2026-11-23 .. Sun 2026-11-29) must make ONLY 1 HTTP request
    items = client.fetch_weekly_schedule(
        access_token=fake_jwt,
        start_date=date(2026, 11, 23),
        end_date=date(2026, 11, 29),
    )

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.scheme == "https"
    assert req.url.path == "/api/v1/lichhoc/lichTuan"
    assert req.url.params["date"] == "2026-11-23"
    assert req.headers["Authorization"] == f"Bearer {fake_jwt}"
    assert req.headers["Accept"] == "application/json"

    assert len(items) == 2
    first, second = items[0], items[1]

    # Event 1: Vietnamese Unicode, dd/MM/yyyy, HH:mm, weekday, Ca 3
    assert first.source_event_id == "101"
    assert first.subject_name == "Kinh tế chính trị Mác - Lênin"
    assert first.class_date == date(2026, 11, 23)
    assert first.weekday == 2
    assert first.session == SessionPeriod.AFTERNOON
    assert first.class_session == "Ca 3"
    assert (first.period_start, first.period_end) == (7, 9)
    assert (first.start_time, first.end_time) == (time(12, 10), time(14, 40))

    # Event 2: Sunday weekday reconciled from date (8), null lecturer & null optional fields
    assert second.source_event_id == "102"
    assert second.class_date == date(2026, 11, 29)
    assert second.weekday == 8
    assert second.lecturer is None
    assert second.note is None
    assert second.have_outline_file is None
    assert second.outline_description is None


def test_parser_handles_suspended_class_empty_body_and_invalid_payloads() -> None:
    # Suspended class (isTamNgung=True)
    suspended = parse_portal_lich_tuan_response(_envelope([_sample_event(id=555, isTamNgung=True)]))
    assert len(suspended) == 1
    assert suspended[0].is_cancelled is True

    # Empty body
    assert parse_portal_lich_tuan_response(_envelope([])) == []

    # success=false
    with pytest.raises(PortalIntegrationError) as exc_false:
        parse_portal_lich_tuan_response(_envelope([], success=False, status=400, message="Lỗi lấy lịch"))
    assert exc_false.value.error_code == "PORTAL_API_ERROR"

    # Missing required fields (id, ngayBatDauHoc, invalid date/time)
    for bad_event in [
        _sample_event(id=None),
        _sample_event(maMonHoc=None, maLopHocPhan=None),
        _sample_event(ngayBatDauHoc="2026-11-23"),  # wrong format (not dd/MM/yyyy)
        _sample_event(tuGio="25:99"),
        _sample_event(tuTiet=None),
    ]:
        with pytest.raises(PortalIntegrationError) as exc_bad:
            parse_portal_lich_tuan_response(_envelope([bad_event]))
        assert exc_bad.value.error_code == "PORTAL_INVALID_PAYLOAD"


def test_client_handles_http_401_500_timeout_and_invalid_json(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    valid_jwt = _make_jwt(3600)

    # 1. HTTP 401 -> SessionManager marks REAUTH_REQUIRED without speculative refresh or extra retries
    calls_401 = 0

    def handler_401(_req: httpx.Request) -> httpx.Response:
        nonlocal calls_401
        calls_401 += 1
        return httpx.Response(401, json={"success": False, "message": "Unauthorized"})

    client_401 = UTHPortalHttpClient(
        refresh_endpoint="",  # unverified -> no refresh
        transport=httpx.MockTransport(handler_401),
    )
    assert client_401.supports_refresh is False
    manager_401 = PortalSessionManager(db_session, client_401, tm, sleep_fn=lambda _: None)
    manager_401.connect_with_token(user_a.id, valid_jwt)

    with pytest.raises(PortalReauthRequiredError):
        manager_401.execute_authenticated(
            user_a.id,
            lambda b: client_401.fetch_weekly_schedule(b.access_token, date(2026, 11, 23), date(2026, 11, 29)),
        )
    assert calls_401 == 1
    assert manager_401.get_status(user_a.id).connection_status == ConnectionStatus.REAUTH_REQUIRED

    # 2. HTTP 500 -> raises PortalHTTPStatusError(500)
    client_500 = UTHPortalHttpClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="Internal Server Error"))
    )
    with pytest.raises(PortalHTTPStatusError) as exc_500:
        client_500.fetch_schedule_for_week(valid_jwt, date(2026, 11, 23))
    assert exc_500.value.status_code == 500

    # 3. Network timeout -> PORTAL_TIMEOUT
    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client_timeout = UTHPortalHttpClient(transport=httpx.MockTransport(handler_timeout))
    with pytest.raises(PortalIntegrationError) as exc_to:
        client_timeout.fetch_schedule_for_week(valid_jwt, date(2026, 11, 23))
    assert exc_to.value.error_code == "PORTAL_TIMEOUT"

    # 4. Invalid JSON (e.g. HTML response) -> PORTAL_INVALID_JSON
    client_html = UTHPortalHttpClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="<html>Login</html>"))
    )
    with pytest.raises(PortalIntegrationError) as exc_json:
        client_html.fetch_schedule_for_week(valid_jwt, date(2026, 11, 23))
    assert exc_json.value.error_code == "PORTAL_INVALID_JSON"


def test_lms_url_sanitization_dedup_suspended_and_no_secret_logging(
    db_session: Session,
    two_users: tuple[User, User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    secret_jwt = _make_jwt(3600)
    secret_lms_token = "LMS_SECRET_TOKEN_DO_NOT_LEAK_987654"

    # Unit check on sanitize_lms_url
    sanitized = sanitize_lms_url(
        f"https://courses.ut.edu.vn/course/view.php?id=505&token={secret_lms_token}&section=2"
    )
    assert sanitized == "https://courses.ut.edu.vn/course/view.php?id=505&section=2"
    assert secret_lms_token not in sanitized
    assert secret_lms_token not in redact_sensitive(
        f"Fetched https://courses.ut.edu.vn/course/view.php?id=505&token={secret_lms_token}"
    )

    # Full sync with duplicate source_event_id + suspended class + LMS token in link
    def handler(_req: httpx.Request) -> httpx.Response:
        evt_active = _sample_event(
            id=7001,
            ngayBatDauHoc="23/11/2026",
            link=f"https://courses.ut.edu.vn/course/view.php?id=505&token={secret_lms_token}&section=1",
        )
        evt_duplicate = _sample_event(
            id=7001,  # duplicate ID in same response
            ngayBatDauHoc="23/11/2026",
            link=f"https://courses.ut.edu.vn/course/view.php?id=505&token={secret_lms_token}&section=1",
        )
        evt_suspended = _sample_event(
            id=7002,
            ngayBatDauHoc="24/11/2026",
            isTamNgung=True,
            link=None,
        )
        return httpx.Response(200, json=_envelope([evt_active, evt_duplicate, evt_suspended]))

    client = UTHPortalHttpClient(transport=httpx.MockTransport(handler))
    manager = PortalSessionManager(db_session, client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, secret_jwt)

    sync_svc = PortalScheduleSyncService(db_session, client, manager)

    # Capture all logs during sync and ensure neither JWT nor LMS token appears
    caplog.handler.addFilter(SensitiveDataFilter())
    with caplog.at_level(logging.DEBUG):
        res = sync_svc.sync_schedules(
            user_a.id,
            start_date=date(2026, 11, 23),
            end_date=date(2026, 11, 29),
        )

    assert res.status == SyncStatus.SUCCESS
    assert res.items_added == 2  # duplicate 7001 deduplicated -> only 7001 and 7002 persisted
    assert res.items_updated == 0

    all_schedules = list(db_session.scalars(select(Schedule).where(Schedule.user_id == user_a.id)).all())
    assert len(all_schedules) == 2

    active_sched = next(s for s in all_schedules if s.source_event_id == "7001")
    suspended_sched = next(s for s in all_schedules if s.source_event_id == "7002")

    assert active_sched.is_deleted is False
    assert suspended_sched.is_deleted is True
    assert active_sched.lms_url == "https://courses.ut.edu.vn/course/view.php?id=505&section=1"
    assert secret_lms_token not in (active_sched.lms_url or "")

    subject = db_session.scalar(select(Subject).where(Subject.user_id == user_a.id))
    assert subject is not None
    assert secret_lms_token not in (subject.lms_url or "")

    # Verify zero secret leakage in logs
    combined_logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret_jwt not in combined_logs
    assert secret_lms_token not in combined_logs
