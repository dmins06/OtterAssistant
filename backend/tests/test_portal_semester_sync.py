from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes.portal import get_portal_provider, get_portal_session_manager
from app.core.security import SecretEncryptor
from app.core.timezone import utc_now
from app.integrations.portal.client import UTHPortalHttpClient
from app.integrations.portal.exceptions import PortalIntegrationError
from app.integrations.portal.schedule_sync import PortalScheduleSyncService
from app.integrations.portal.semester import resolve_semester_range
from app.integrations.portal.session_manager import PortalSessionManager
from app.integrations.portal.token_manager import PortalTokenManager
from app.integrations.telegram.client import MockTelegramBotClient
from app.models.academic import Schedule, Subject
from app.models.connection import SyncLog
from app.models.enums import ConnectionStatus, SessionPeriod, SyncStatus
from app.models.user import AccountLinking, User
from app.notifications.service import NotificationService
from app.scheduler.jobs import run_portal_semester_sync
from app.scheduler.locks import RedisDistributedLock
from app.scheduler.manager import create_scheduler
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService
from tests.conftest import auth_headers


class FakeRedisForLock:
    """Deterministic in-memory Redis double supporting SET NX EX and ownership Lua scripts."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.renew_calls = 0

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def eval(self, script: str, _numkeys: int, key: str, token: str, *args: Any) -> int:
        if "pexpire" in script:
            self.renew_calls += 1
            if self.store.get(key) == token:
                self.ttls[key] = int(args[0]) // 1000
                return 1
            return 0
        if "del" in script:
            if self.store.get(key) == token:
                del self.store[key]
                return 1
            return 0
        return 0

    def close(self) -> None:
        pass


def _make_jwt(exp_delta_seconds: int = 3600) -> str:
    exp = int((datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)).timestamp())
    return jwt.encode({"sub": "2251120001", "exp": exp}, "semester-test-secret", algorithm="HS256")


def _make_token_manager() -> PortalTokenManager:
    return PortalTokenManager(SecretEncryptor(key=Fernet.generate_key().decode("utf-8")))


def _portal_event(event_id: int, date_str: str, subject_code: str, name: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "ngayBatDauHoc": date_str,
        "tenPhong": "C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
        "thu": 2,
        "tuTiet": 4,
        "denTiet": 6,
        "maLopHocPhan": subject_code,
        "maMonHoc": subject_code,
        "tenMonHoc": name,
        "giangVien": "TS. Nguyễn Văn A",
        "isTamNgung": False,
        "tuGio": "09:25",
        "denGio": "11:55",
        "link": f"https://courses.ut.edu.vn/course/view.php?id={event_id}&token=SECRET_LMS_TOKEN_123",
        "coSoToDisplay": "CS1",
    }


def _envelope(body: list[dict[str, Any]]) -> dict[str, Any]:
    return {"success": True, "status": 200, "message": None, "body": body, "token": None, "timestamp": "2026-11-23T00:00:00Z"}


def test_resolve_semester_range_requires_end_date_and_never_defaults() -> None:
    with pytest.raises(PortalIntegrationError) as exc:
        resolve_semester_range(
            today=date(2026, 11, 25),
            configured_start="",
            configured_end="",
        )
    assert exc.value.error_code == "SEMESTER_RANGE_NOT_CONFIGURED"


def test_resolve_semester_range_rejects_malformed_end_date() -> None:
    with pytest.raises(PortalIntegrationError) as exc:
        resolve_semester_range(
            today=date(2026, 11, 25),
            configured_end="31/12/2026",  # wrong format, expected YYYY-MM-DD
        )
    assert exc.value.error_code == "SEMESTER_RANGE_NOT_CONFIGURED"


def test_resolve_semester_range_generates_monday_anchors_across_year_boundary() -> None:
    # Today is Wednesday 2026-12-23 -> current week Monday is 2026-12-21
    # End date is Sunday 2027-01-10 -> 3 weeks: 2026-12-21, 2026-12-28, 2027-01-04
    sem = resolve_semester_range(
        today=date(2026, 12, 23),
        configured_start="2026-09-01",
        configured_end="2027-01-10",
    )
    assert sem.start_date == date(2026, 12, 21)
    assert sem.end_date == date(2027, 1, 10)
    assert sem.week_anchors == [
        date(2026, 12, 21),
        date(2026, 12, 28),
        date(2027, 1, 4),
    ]


def test_resolve_semester_range_uses_future_configured_start_date() -> None:
    # If configured_start is in the future relative to today, start from configured_start
    sem = resolve_semester_range(
        today=date(2026, 8, 15),
        configured_start="2026-09-07",
        configured_end="2026-09-20",
    )
    assert sem.start_date == date(2026, 9, 7)
    assert sem.week_anchors == [date(2026, 9, 7), date(2026, 9, 14)]


def test_resolve_semester_range_rejects_ended_semester_and_excessive_span() -> None:
    with pytest.raises(PortalIntegrationError) as exc_past:
        resolve_semester_range(
            today=date(2027, 2, 1),
            configured_end="2027-01-15",
        )
    assert exc_past.value.error_code == "INVALID_SEMESTER_RANGE"

    with pytest.raises(PortalIntegrationError) as exc_large:
        resolve_semester_range(
            today=date(2026, 11, 23),
            configured_end="2028-11-23",
            max_weeks=24,
        )
    assert exc_large.value.error_code == "SEMESTER_RANGE_TOO_LARGE"


def test_distributed_lock_ownership_renewal_and_safe_loss_detection() -> None:
    fake_redis = FakeRedisForLock()
    lock = RedisDistributedLock(redis_client=fake_redis)

    with lock.acquire("portal:sync:user:1", ttl_seconds=30, auto_renew=False) as lease1:
        assert bool(lease1) is True
        assert lease1.lost is False
        # Concurrent acquisition for same user is blocked
        with lock.acquire("portal:sync:user:1", ttl_seconds=30, auto_renew=False) as lease2:
            assert bool(lease2) is False

        # Renewal succeeds while we own the token
        assert lease1.renew() is True
        assert fake_redis.renew_calls == 1

        # Simulate lock stolen/replaced by another worker -> renewal detects loss & release won't delete other worker's lock
        fake_redis.store[lease1.key] = "other-worker-token"
        assert lease1.renew() is False
        assert lease1.lost is True
        assert bool(lease1) is False

    # Exiting context MUST NOT delete the other worker's token
    assert fake_redis.store["otter:lock:portal:sync:user:1"] == "other-worker-token"


def test_semester_sync_multi_week_success_and_partial_failure_preserves_failed_week(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    fake_redis = FakeRedisForLock()

    # Seed existing schedule in Week 2 (2026-12-01) -> MUST survive when Week 2 fails!
    existing_subj = SubjectService(db_session).upsert_subject(
        user_a.id, SubjectUpsert(subject_code="KEEPW2", name="Môn tuần 2 cũ")
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=existing_subj.id,
            source_event_id="keep-week2-evt",
            class_date=date(2026, 12, 1),
            weekday=3,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="B101",
            campus="CS1",
        ),
    )

    requested_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        d = request.url.params["date"]
        requested_dates.append(d)
        if d == "2026-11-23":
            return httpx.Response(200, json=_envelope([_portal_event(1001, "23/11/2026", "SUB1", "Học sâu")]))
        if d == "2026-11-30":
            return httpx.Response(500, text="Portal week 2 outage")
        if d == "2026-12-07":
            return httpx.Response(200, json=_envelope([_portal_event(1003, "07/12/2026", "SUB3", "Trực quan hóa")]))
        return httpx.Response(200, json=_envelope([]))

    client = UTHPortalHttpClient(transport=httpx.MockTransport(handler))
    manager = PortalSessionManager(db_session, client, tm, max_5xx_retries=0, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, _make_jwt(3600))

    sync_svc = PortalScheduleSyncService(
        db=db_session,
        provider=client,
        session_manager=manager,
        lock_provider=RedisDistributedLock(redis_client=fake_redis),
        week_delay_seconds=0.0,
        sleep_fn=lambda _: None,
    )

    res = sync_svc.sync_semester_schedules(
        user_a.id,
        today=date(2026, 11, 23),
        configured_end="2026-12-13",  # 3 weeks: 2026-11-23, 2026-11-30, 2026-12-07
    )

    assert requested_dates == ["2026-11-23", "2026-11-30", "2026-12-07"]
    assert res.status == SyncStatus.PARTIAL
    assert res.weeks_total == 3
    assert res.weeks_succeeded == 2
    assert res.weeks_failed == 1
    assert res.items_added == 2

    # Week 1 + Week 3 persisted, AND Week 2's pre-existing schedule preserved (not soft-deleted!)
    active_ids = {
        s.source_event_id
        for s in db_session.scalars(
            select(Schedule).where(Schedule.user_id == user_a.id, Schedule.is_deleted.is_(False))
        ).all()
    }
    assert active_ids == {"1001", "keep-week2-evt", "1003"}

    # LMS token sanitized on persisted schedules
    for s in db_session.scalars(select(Schedule).where(Schedule.user_id == user_a.id)).all():
        assert "SECRET_LMS_TOKEN_123" not in (s.lms_url or "")

    log_row = db_session.get(SyncLog, res.sync_log_id)
    assert log_row is not None
    assert log_row.status == SyncStatus.PARTIAL


def test_semester_sync_fails_fast_on_401_and_blocks_concurrent_user_sync(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    fake_redis = FakeRedisForLock()
    requested_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        d = request.url.params["date"]
        requested_dates.append(d)
        if d == "2026-11-23":
            return httpx.Response(200, json=_envelope([_portal_event(2001, "23/11/2026", "SUB1", "Học sâu")]))
        return httpx.Response(401, json={"success": False, "message": "Token expired"})

    client = UTHPortalHttpClient(refresh_endpoint="", transport=httpx.MockTransport(handler))
    manager = PortalSessionManager(db_session, client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, _make_jwt(3600))
    lock_provider = RedisDistributedLock(redis_client=fake_redis)

    sync_svc = PortalScheduleSyncService(
        db=db_session,
        provider=client,
        session_manager=manager,
        lock_provider=lock_provider,
        week_delay_seconds=0.0,
        sleep_fn=lambda _: None,
    )

    # 1. Concurrent sync blocked when per-user lock is already held
    with lock_provider.acquire(f"portal:sync:user:{user_a.id}", ttl_seconds=60, auto_renew=False):
        blocked = sync_svc.sync_semester_schedules(
            user_a.id,
            today=date(2026, 11, 23),
            configured_end="2026-12-13",
        )
        assert blocked.status == SyncStatus.FAILED
        assert blocked.error_code == "PORTAL_SYNC_IN_PROGRESS"
        assert requested_dates == []

    # 2. Fail-fast on HTTP 401 at Week 2 -> does NOT call Week 3!
    res = sync_svc.sync_semester_schedules(
        user_a.id,
        today=date(2026, 11, 23),
        configured_end="2026-12-13",
    )
    assert requested_dates == ["2026-11-23", "2026-11-30"]  # Week 3 skipped (fail-fast)
    assert res.status == SyncStatus.PARTIAL
    assert res.reauth_required is True
    assert res.weeks_succeeded == 1
    assert res.weeks_failed == 2
    assert manager.get_status(user_a.id).connection_status == ConnectionStatus.REAUTH_REQUIRED


def test_sync_semester_api_endpoint_and_missing_config(
    client: TestClient,
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()

    def handler(request: httpx.Request) -> httpx.Response:
        d = request.url.params["date"]
        if d == "2026-11-23":
            return httpx.Response(200, json=_envelope([_portal_event(3001, "23/11/2026", "API01", "Học sâu")]))
        return httpx.Response(200, json=_envelope([_portal_event(3002, "30/11/2026", "API02", "Hệ điều hành")]))

    portal_client = UTHPortalHttpClient(transport=httpx.MockTransport(handler))
    manager = PortalSessionManager(db_session, portal_client, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, _make_jwt(3600))

    client.app.dependency_overrides[get_portal_provider] = lambda: portal_client  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_portal_session_manager] = lambda: manager  # type: ignore[attr-defined]

    # 1. Missing configured end_date and no payload end_date -> SEMESTER_RANGE_NOT_CONFIGURED
    missing_resp = client.post(
        "/api/v1/integrations/portal/sync-semester",
        headers=auth_headers(user_a),
        json={},
    )
    assert missing_resp.status_code == 200
    assert missing_resp.json()["status"] == "FAILED"
    assert missing_resp.json()["error_code"] == "SEMESTER_RANGE_NOT_CONFIGURED"

    # 2. Explicit semester range -> syncs both weeks cleanly
    ok_resp = client.post(
        "/api/v1/integrations/portal/sync-semester",
        headers=auth_headers(user_a),
        json={"start_date": "2026-11-23", "end_date": "2026-12-06"},
    )
    assert ok_resp.status_code == 200
    body = ok_resp.json()
    assert body["status"] == "SUCCESS"
    assert body["weeks_total"] == 2
    assert body["weeks_succeeded"] == 2
    assert body["items_added"] == 2
    assert "SECRET_LMS_TOKEN_123" not in ok_resp.text


def test_scheduler_portal_semester_sync_skips_unconfigured_and_notifies_telegram_on_reauth(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    tm = _make_token_manager()
    fake_redis = FakeRedisForLock()
    tg = MockTelegramBotClient()
    notif_svc = NotificationService(db_session, tg)

    # Link both users to Telegram
    db_session.add_all(
        [
            AccountLinking(
                user_id=user_a.id,
                provider="telegram",
                external_user_id="tg-user-a",
                is_active=True,
                last_interaction_at=utc_now(),
            ),
            AccountLinking(
                user_id=user_b.id,
                provider="telegram",
                external_user_id="tg-user-b",
                is_active=True,
                last_interaction_at=utc_now(),
            ),
        ]
    )
    db_session.commit()

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # User A's token works; User B's token returns 401
        if "user_b_sub" in request.headers.get("Authorization", ""):
            return httpx.Response(401, json={"success": False, "message": "Expired"})
        return httpx.Response(200, json=_envelope([_portal_event(4001, "23/11/2026", "SCH01", "Học sâu")]))

    portal_client = UTHPortalHttpClient(refresh_endpoint="", transport=httpx.MockTransport(handler))
    manager = PortalSessionManager(db_session, portal_client, tm, sleep_fn=lambda _: None)

    # User A: valid JWT
    manager.connect_with_token(user_a.id, _make_jwt(3600))
    # User B: token that passes local exp check, then gets 401 from Portal
    user_b_jwt = jwt.encode(
        {"sub": "2251120002", "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
        "user_b_secret",
        algorithm="HS256",
    )
    manager.connect_with_token(user_b.id, user_b_jwt)

    # Make handler detect user_b_jwt cleanly
    def auth_aware_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.headers.get("Authorization") == f"Bearer {user_b_jwt}":
            return httpx.Response(401, json={"success": False, "message": "Expired"})
        return httpx.Response(200, json=_envelope([_portal_event(4001, "23/11/2026", "SCH01", "Học sâu")]))

    portal_client = UTHPortalHttpClient(refresh_endpoint="", transport=httpx.MockTransport(auth_aware_handler))
    lock_provider = RedisDistributedLock(redis_client=fake_redis)

    # 1. Unconfigured semester end date -> scheduler skips cleanly (0 Portal calls)
    skipped = run_portal_semester_sync(
        db_session,
        notif_svc,
        provider=portal_client,
        token_manager=tm,
        lock_provider=lock_provider,
        now_utc=datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc),
        configured_end="",
        week_delay_seconds=0.0,
    )
    assert skipped == 0
    assert calls == 0

    # 2. Configured semester end date -> User A syncs cleanly; User B hits 401 -> REAUTH_REQUIRED + Telegram alert
    synced = run_portal_semester_sync(
        db_session,
        notif_svc,
        provider=portal_client,
        token_manager=tm,
        lock_provider=lock_provider,
        now_utc=datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc),
        configured_end="2026-11-29",
        week_delay_seconds=0.0,
    )
    assert synced == 1
    assert manager.get_status(user_a.id).connection_status == ConnectionStatus.AUTHENTICATED
    assert manager.get_status(user_b.id).connection_status == ConnectionStatus.REAUTH_REQUIRED
    assert len(tg.sent_messages) == 1
    assert tg.sent_messages[0]["chat_id"] == "tg-user-b"
    assert "hết hạn" in tg.sent_messages[0]["text"]

    # 3. Verify APScheduler registers portal_semester_sync with 6-hour interval
    scheduler = create_scheduler()
    job = scheduler.get_job("portal_semester_sync")
    assert job is not None
    assert job.trigger.interval == timedelta(hours=6)
