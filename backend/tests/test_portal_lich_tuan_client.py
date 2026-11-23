from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from typing import Any

from cryptography.fernet import Fernet
import httpx
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
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
from app.models.connection import SyncLog
from app.models.enums import ConnectionStatus, SessionPeriod, SyncStatus
from app.models.user import User
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService


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


def test_portal_sync_atomic_rollback_on_mid_loop_failure_and_isolated_sync_log(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    valid_jwt = _make_jwt(3600)

    # Seed an existing schedule inside the target week to verify it is NOT soft-deleted on rollback
    existing_subject = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="EXISTING01", name="Môn học đã có"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=existing_subject.id,
            source_event_id="existing-evt-1",
            class_date=date(2026, 11, 24),
            weekday=3,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="B303",
            campus="CS1",
        ),
    )

    events = [
        _sample_event(id=8001, maMonHoc="NEW001", tenMonHoc="Môn mới 1", ngayBatDauHoc="23/11/2026"),
        _sample_event(id=8002, maMonHoc="NEW002", tenMonHoc="Môn mới 2", ngayBatDauHoc="25/11/2026"),
    ]
    client = UTHPortalHttpClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_envelope(events)))
    )
    manager = PortalSessionManager(db_session, client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, valid_jwt)
    sync_svc = PortalScheduleSyncService(db_session, client, manager)

    # Inject a mid-loop failure on the 2nd schedule upsert (after 1st subject + schedule were flushed)
    original_upsert = sync_svc.schedule_service.upsert_schedule
    call_count = 0

    def failing_on_second(uid: int, payload: ScheduleUpsert, commit: bool = True) -> Schedule:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Simulated DB write failure mid-sync")
        return original_upsert(uid, payload, commit=commit)

    sync_svc.schedule_service.upsert_schedule = failing_on_second  # type: ignore[method-assign]

    failed_res = sync_svc.sync_schedules(
        user_a.id,
        start_date=date(2026, 11, 23),
        end_date=date(2026, 11, 29),
    )
    assert failed_res.status == SyncStatus.FAILED
    assert failed_res.items_added == 0
    assert failed_res.error_code == "PORTAL_SYNC_TRANSACTION_ERROR"

    # 1. Partial writes (NEW001 / 8001) MUST be completely rolled back
    subjects_after_fail = list(db_session.scalars(select(Subject).where(Subject.user_id == user_a.id)).all())
    assert [s.subject_code for s in subjects_after_fail] == ["EXISTING01"]

    schedules_after_fail = list(
        db_session.scalars(select(Schedule).where(Schedule.user_id == user_a.id)).all()
    )
    assert len(schedules_after_fail) == 1
    assert schedules_after_fail[0].source_event_id == "existing-evt-1"
    assert schedules_after_fail[0].is_deleted is False

    # 2. SyncLog MUST be persisted as FAILED (not rolled back or stuck in RUNNING)
    failed_log = db_session.get(SyncLog, failed_res.sync_log_id)
    assert failed_log is not None
    assert failed_log.status == SyncStatus.FAILED
    assert failed_log.finished_at is not None
    assert failed_log.error_code == "PORTAL_SYNC_TRANSACTION_ERROR"

    # 3. Restore normal upsert -> subsequent sync commits atomically and marks SyncLog=SUCCESS
    sync_svc.schedule_service.upsert_schedule = original_upsert  # type: ignore[method-assign]
    ok_res = sync_svc.sync_schedules(
        user_a.id,
        start_date=date(2026, 11, 23),
        end_date=date(2026, 11, 29),
    )
    assert ok_res.status == SyncStatus.SUCCESS
    assert ok_res.items_added == 2
    assert ok_res.items_removed == 1  # existing-evt-1 soft-deleted now that sync succeeded

    ok_log = db_session.get(SyncLog, ok_res.sync_log_id)
    assert ok_log is not None
    assert ok_log.status == SyncStatus.SUCCESS
    assert ok_log.finished_at is not None


def test_portal_sync_final_commit_failure_rolls_back_soft_delete_and_marks_sync_log_failed(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    valid_jwt = _make_jwt(3600)

    # Seed existing schedule inside [2026-11-23, 2026-11-25] so Transaction 2 will mark it is_deleted=True
    existing_subject = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="EXISTING02", name="Môn học cũ"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=existing_subject.id,
            source_event_id="existing-evt-commit-fail",
            class_date=date(2026, 11, 24),
            weekday=3,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="C007",
            campus="CS1",
        ),
    )

    events = [
        _sample_event(id=9101, maMonHoc="COMMITNEW1", tenMonHoc="Môn mới A", ngayBatDauHoc="23/11/2026"),
        _sample_event(id=9102, maMonHoc="COMMITNEW2", tenMonHoc="Môn mới B", ngayBatDauHoc="25/11/2026"),
    ]
    client = UTHPortalHttpClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_envelope(events)))
    )
    manager = PortalSessionManager(db_session, client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, valid_jwt)
    sync_svc = PortalScheduleSyncService(db_session, client, manager)

    # Commit #1: Transaction 1 (SyncLog=RUNNING) -> succeeds
    # Commit #2: Transaction 2 final commit (after upserts + soft-delete) -> fails ONCE with SQLAlchemyError
    # Commit #3: Transaction 3 (_mark_sync_log_failed) -> succeeds
    real_commit = db_session.commit
    commit_calls = 0

    def fail_only_on_transaction_2_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise SQLAlchemyError("Simulated commit failure on Transaction 2")
        real_commit()

    db_session.commit = fail_only_on_transaction_2_commit  # type: ignore[method-assign]
    try:
        res = sync_svc.sync_schedules(
            user_a.id,
            start_date=date(2026, 11, 23),
            end_date=date(2026, 11, 29),
        )
    finally:
        db_session.commit = real_commit  # type: ignore[method-assign]

    assert commit_calls == 3
    assert res.status == SyncStatus.FAILED
    assert res.items_added == 0
    assert res.items_updated == 0
    assert res.items_removed == 0

    # New subjects & schedules MUST NOT exist
    subjects = list(db_session.scalars(select(Subject).where(Subject.user_id == user_a.id)).all())
    assert [s.subject_code for s in subjects] == ["EXISTING02"]

    schedules = list(db_session.scalars(select(Schedule).where(Schedule.user_id == user_a.id)).all())
    assert len(schedules) == 1
    assert schedules[0].source_event_id == "existing-evt-commit-fail"
    assert schedules[0].is_deleted is False  # Soft-delete rolled back completely

    # SyncLog MUST be persisted as FAILED with finished_at set
    log_row = db_session.get(SyncLog, res.sync_log_id)
    assert log_row is not None
    assert log_row.status == SyncStatus.FAILED
    assert log_row.finished_at is not None


def test_portal_sync_unexpected_fetch_exception_marks_sync_log_failed(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    valid_jwt = _make_jwt(3600)

    class ExplodingProvider(UTHPortalHttpClient):
        def fetch_weekly_schedule(self, access_token, start_date, end_date, csrf_token=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("Unexpected runtime bug during fetch")

    provider = ExplodingProvider()
    manager = PortalSessionManager(db_session, provider, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, valid_jwt)
    sync_svc = PortalScheduleSyncService(db_session, provider, manager)

    res = sync_svc.sync_schedules(
        user_a.id,
        start_date=date(2026, 11, 23),
        end_date=date(2026, 11, 29),
    )
    assert res.status == SyncStatus.FAILED
    assert res.error_code == "PORTAL_FETCH_UNEXPECTED_ERROR"

    log_row = db_session.get(SyncLog, res.sync_log_id)
    assert log_row is not None
    assert log_row.status == SyncStatus.FAILED
    assert log_row.error_code == "PORTAL_FETCH_UNEXPECTED_ERROR"
    assert log_row.finished_at is not None


def test_portal_sync_fetch_exception_preserved_when_mark_sync_log_failed_errors(
    db_session: Session,
    two_users: tuple[User, User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    secret_jwt = _make_jwt(3600)

    class ExplodingProvider(UTHPortalHttpClient):
        def fetch_weekly_schedule(self, access_token, start_date, end_date, csrf_token=None):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"Unexpected fetch error with token={secret_jwt}")

    provider = ExplodingProvider()
    manager = PortalSessionManager(db_session, provider, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, secret_jwt)
    sync_svc = PortalScheduleSyncService(db_session, provider, manager)

    # Commit #1: Transaction 1 (SyncLog=RUNNING) succeeds
    # Commit #2: Transaction 3 (_mark_sync_log_failed) ALSO fails with SQLAlchemyError
    real_commit = db_session.commit
    commit_calls = 0

    def fail_on_mark_failed_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise SQLAlchemyError("Secondary DB failure while updating SyncLog")
        real_commit()

    caplog.handler.addFilter(SensitiveDataFilter())
    db_session.commit = fail_on_mark_failed_commit  # type: ignore[method-assign]
    try:
        with caplog.at_level(logging.WARNING):
            res = sync_svc.sync_schedules(
                user_a.id,
                start_date=date(2026, 11, 23),
                end_date=date(2026, 11, 29),
            )
    finally:
        db_session.commit = real_commit  # type: ignore[method-assign]

    # Primary failure MUST be preserved (not replaced by secondary SQLAlchemyError)
    assert commit_calls == 2
    assert res.status == SyncStatus.FAILED
    assert res.error_code == "PORTAL_FETCH_UNEXPECTED_ERROR"
    assert "RuntimeError" in (res.error_message or "")

    # Verify primary failure was logged BEFORE secondary failure, and zero secret leaked
    messages = [rec.getMessage() for rec in caplog.records]
    primary_idx = next(i for i, m in enumerate(messages) if "PORTAL_FETCH_UNEXPECTED_ERROR" in m)
    secondary_idx = next(i for i, m in enumerate(messages) if "Secondary failure while marking Portal SyncLog" in m)
    assert primary_idx < secondary_idx
    assert secret_jwt not in "\n".join(messages)


def test_portal_sync_tx2_commit_failure_preserved_when_mark_sync_log_failed_errors(
    db_session: Session,
    two_users: tuple[User, User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    secret_jwt = _make_jwt(3600)

    existing_subject = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="EXISTING03", name="Môn cũ bền vững"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=existing_subject.id,
            source_event_id="existing-evt-double-fail",
            class_date=date(2026, 11, 24),
            weekday=3,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="C007",
            campus="CS1",
        ),
    )

    events = [
        _sample_event(id=9201, maMonHoc="DBLNEW1", tenMonHoc="Môn mới 1", ngayBatDauHoc="23/11/2026"),
        _sample_event(id=9202, maMonHoc="DBLNEW2", tenMonHoc="Môn mới 2", ngayBatDauHoc="25/11/2026"),
    ]
    client = UTHPortalHttpClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_envelope(events)))
    )
    manager = PortalSessionManager(db_session, client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, secret_jwt)
    sync_svc = PortalScheduleSyncService(db_session, client, manager)

    # Commit #1: Transaction 1 (SyncLog=RUNNING) succeeds
    # Commit #2: Transaction 2 final commit fails with SQLAlchemyError
    # Commit #3: Transaction 3 (_mark_sync_log_failed) ALSO fails with SQLAlchemyError
    real_commit = db_session.commit
    commit_calls = 0

    def fail_on_tx2_and_tx3_commits() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls in (2, 3):
            raise SQLAlchemyError(f"Simulated DB commit error #{commit_calls}")
        real_commit()

    caplog.handler.addFilter(SensitiveDataFilter())
    db_session.commit = fail_on_tx2_and_tx3_commits  # type: ignore[method-assign]
    try:
        with caplog.at_level(logging.WARNING):
            res = sync_svc.sync_schedules(
                user_a.id,
                start_date=date(2026, 11, 23),
                end_date=date(2026, 11, 29),
            )
    finally:
        db_session.commit = real_commit  # type: ignore[method-assign]

    # Primary Transaction 2 failure preserved; no unhandled secondary exception
    assert commit_calls == 3
    assert res.status == SyncStatus.FAILED
    assert res.error_code == "PORTAL_SYNC_TRANSACTION_ERROR"

    # Transaction 2 changes (new subject, new schedule, soft-delete) MUST be rolled back
    subjects = list(db_session.scalars(select(Subject).where(Subject.user_id == user_a.id)).all())
    assert [s.subject_code for s in subjects] == ["EXISTING03"]

    schedules = list(db_session.scalars(select(Schedule).where(Schedule.user_id == user_a.id)).all())
    assert len(schedules) == 1
    assert schedules[0].source_event_id == "existing-evt-double-fail"
    assert schedules[0].is_deleted is False

    # Primary warning logged before secondary error, and zero secret leaked
    messages = [rec.getMessage() for rec in caplog.records]
    primary_idx = next(i for i, m in enumerate(messages) if "Portal sync transaction rolled back" in m)
    secondary_idx = next(i for i, m in enumerate(messages) if "Secondary failure while marking Portal SyncLog" in m)
    assert primary_idx < secondary_idx
    assert secret_jwt not in "\n".join(messages)
