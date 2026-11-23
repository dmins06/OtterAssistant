from datetime import date, datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.portal import get_portal_provider, get_portal_session_manager
from app.core.security import SecretEncryptor
from app.integrations.portal.client import MockPortalClient
from app.integrations.portal.exceptions import PortalHTTPStatusError, PortalReauthRequiredError
from app.integrations.portal.parser import parse_uth_room_text
from app.integrations.portal.schedule_sync import PortalScheduleSyncService
from app.integrations.portal.session_manager import PortalSessionManager
from app.integrations.portal.token_manager import PortalTokenBundle, PortalTokenManager
from app.models.academic import Schedule
from app.models.connection import PortalConnection, SyncLog
from app.models.enums import ConnectionStatus, SyncStatus
from app.models.user import User
from tests.conftest import auth_headers


def _make_jwt(exp_delta_seconds: int) -> str:
    exp = int((datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)).timestamp())
    return jwt.encode({"sub": "2251120001", "exp": exp}, "portal-test-secret", algorithm="HS256")


def _make_token_manager() -> PortalTokenManager:
    return PortalTokenManager(SecretEncryptor(key=Fernet.generate_key().decode("utf-8")))


def test_uth_room_text_parser_matches_real_portal_formats() -> None:
    c007 = parse_uth_room_text("Phòng: C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)")
    assert c007.room == "C007"
    assert c007.campus == "CS1"
    assert c007.location == "P.Thạnh Mỹ Tây, TP.HCM"

    hall = parse_uth_room_text("Phòng: Hội Trường Hội Nhập CS1 - (P.Thạnh Mỹ Tây, TP.HCM)")
    assert hall.room == "Hội Trường Hội Nhập"
    assert hall.campus == "CS1"
    assert hall.location == "P.Thạnh Mỹ Tây, TP.HCM"


def test_portal_session_connect_encrypts_tokens_and_rejects_expired_jwt(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    manager = PortalSessionManager(db_session, MockPortalClient(), tm)

    # Reject already expired JWT
    expired_jwt = _make_jwt(-60)
    with pytest.raises(PortalReauthRequiredError):
        manager.connect_with_token(user_a.id, expired_jwt)

    # Connect with valid JWT -> stored encrypted (raw token never in DB)
    valid_jwt = _make_jwt(3600)
    status = manager.connect_with_token(user_a.id, valid_jwt, refresh_token="refresh-secret-token")
    assert status.connection_status == ConnectionStatus.AUTHENTICATED
    assert status.access_token_expires_at is not None

    conn = db_session.scalar(select(PortalConnection).where(PortalConnection.user_id == user_a.id))
    assert conn is not None
    assert conn.encrypted_credentials is not None
    assert valid_jwt not in conn.encrypted_credentials
    assert "refresh-secret-token" not in conn.encrypted_credentials


def test_portal_proactive_refresh_and_rotation_when_supported(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()

    class RotatingRefreshProvider(MockPortalClient):
        supports_refresh = True

        def refresh_access_token(self, refresh_token: str) -> PortalTokenBundle:
            assert refresh_token == "old-refresh-token"
            return PortalTokenBundle(
                access_token=_make_jwt(7200),
                refresh_token="new-rotated-refresh-token",
            )

    manager = PortalSessionManager(
        db_session,
        RotatingRefreshProvider(supports_refresh=True),
        tm,
        refresh_buffer_seconds=300,
    )
    # Token expires in 60s (< 300s buffer) -> triggers proactive refresh
    manager.connect_with_token(user_a.id, _make_jwt(60), refresh_token="old-refresh-token")
    bundle = manager.ensure_valid_bundle(user_a.id)
    assert bundle.refresh_token == "new-rotated-refresh-token"

    snap = manager.get_status(user_a.id)
    assert snap.connection_status == ConnectionStatus.AUTHENTICATED
    assert snap.last_refreshed_at is not None


def test_portal_http_401_single_retry_403_distinction_429_and_5xx(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    slept: list[float] = []

    class RefreshableProvider(MockPortalClient):
        supports_refresh = True

        def refresh_access_token(self, refresh_token: str) -> PortalTokenBundle:
            return PortalTokenBundle(access_token=_make_jwt(3600), refresh_token=refresh_token)

    manager = PortalSessionManager(
        db_session,
        RefreshableProvider(supports_refresh=True),
        tm,
        max_5xx_retries=2,
        sleep_fn=lambda d: slept.append(d),
    )
    manager.connect_with_token(user_a.id, _make_jwt(3600), refresh_token="rt-1")

    # 1. HTTP 401 -> refreshes once and succeeds on retry
    calls_401 = 0

    def req_401(_bundle: PortalTokenBundle) -> str:
        nonlocal calls_401
        calls_401 += 1
        if calls_401 == 1:
            raise PortalHTTPStatusError(401, "Unauthorized")
        return "recovered"

    assert manager.execute_authenticated(user_a.id, req_401) == "recovered"
    assert calls_401 == 2

    # 2. HTTP 403 CSRF -> marks ERROR with PORTAL_CSRF_ERROR (NOT REAUTH_REQUIRED)
    with pytest.raises(PortalHTTPStatusError):
        manager.execute_authenticated(
            user_a.id,
            lambda _b: (_ for _ in ()).throw(
                PortalHTTPStatusError(403, "CSRF mismatch", is_csrf_error=True)
            ),
        )
    snap = manager.get_status(user_a.id)
    assert snap.connection_status == ConnectionStatus.ERROR
    assert snap.last_error_code == "PORTAL_CSRF_ERROR"

    # Re-authenticate for 429 & 5xx tests
    manager.connect_with_token(user_a.id, _make_jwt(3600), refresh_token="rt-1")

    # 3. HTTP 429 respects Retry-After and succeeds
    calls_429 = 0

    def req_429(_bundle: PortalTokenBundle) -> str:
        nonlocal calls_429
        calls_429 += 1
        if calls_429 == 1:
            raise PortalHTTPStatusError(429, "Too Many Requests", retry_after_seconds=3.0)
        return "ok-after-429"

    assert manager.execute_authenticated(user_a.id, req_429) == "ok-after-429"
    assert slept == [3.0]

    # 4. Persistent HTTP 503 retries bounded times then raises
    calls_5xx = 0

    def req_5xx(_bundle: PortalTokenBundle) -> str:
        nonlocal calls_5xx
        calls_5xx += 1
        raise PortalHTTPStatusError(503, "Service Unavailable")

    with pytest.raises(PortalHTTPStatusError):
        manager.execute_authenticated(user_a.id, req_5xx)
    assert calls_5xx == 3  # 1 initial + 2 retries


def test_portal_schedule_sync_and_preserves_data_on_5xx_failure(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    tm = _make_token_manager()
    provider = MockPortalClient()
    manager = PortalSessionManager(db_session, provider, tm, sleep_fn=lambda _: None)
    manager.connect_with_token(user_a.id, _make_jwt(3600))

    sync_svc = PortalScheduleSyncService(db_session, provider, manager)
    res1 = sync_svc.sync_schedules(user_a.id, start_date=date(2026, 12, 7), end_date=date(2026, 12, 13))
    assert res1.status == SyncStatus.SUCCESS
    assert res1.is_mock is True
    assert res1.items_added == 2
    assert res1.items_updated == 0

    # Re-sync same window -> idempotent (0 added, 2 updated, 0 removed)
    res2 = sync_svc.sync_schedules(user_a.id, start_date=date(2026, 12, 7), end_date=date(2026, 12, 13))
    assert res2.status == SyncStatus.SUCCESS
    assert res2.items_added == 0
    assert res2.items_updated == 2

    # Failing provider (500) must NOT wipe previously synced MySQL schedules
    class FailingProvider(MockPortalClient):
        def fetch_weekly_schedule(self, access_token, start_date, end_date, csrf_token=None):  # type: ignore[no-untyped-def]
            raise PortalHTTPStatusError(500, "Portal down")

    fail_manager = PortalSessionManager(db_session, FailingProvider(), tm, max_5xx_retries=1, sleep_fn=lambda _: None)
    fail_sync = PortalScheduleSyncService(db_session, FailingProvider(), fail_manager)
    res_fail = fail_sync.sync_schedules(user_a.id, start_date=date(2026, 12, 7), end_date=date(2026, 12, 13))
    assert res_fail.status == SyncStatus.FAILED
    assert res_fail.error_code == "HTTP_500"

    preserved = list(
        db_session.scalars(
            select(Schedule).where(Schedule.user_id == user_a.id, Schedule.is_deleted.is_(False))
        ).all()
    )
    assert len(preserved) == 2

    logs = list(db_session.scalars(select(SyncLog).where(SyncLog.user_id == user_a.id)).all())
    assert len(logs) == 3


def test_portal_api_never_leaks_jwt_and_enforces_user_isolation(
    client: TestClient,
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    tm = _make_token_manager()
    mock_provider = MockPortalClient()
    app = client.app
    app.dependency_overrides[get_portal_provider] = lambda: mock_provider  # type: ignore[attr-defined]
    app.dependency_overrides[get_portal_session_manager] = lambda: PortalSessionManager(  # type: ignore[attr-defined]
        db=db_session,
        provider=mock_provider,
        token_manager=tm,
    )

    secret_jwt = _make_jwt(3600)
    connect_resp = client.post(
        "/api/v1/integrations/portal/session",
        headers=auth_headers(user_a),
        json={"access_token": secret_jwt, "refresh_token": "super-secret-rt"},
    )
    assert connect_resp.status_code == 200
    assert secret_jwt not in connect_resp.text
    assert "super-secret-rt" not in connect_resp.text
    assert connect_resp.json()["connection_status"] == "AUTHENTICATED"

    # User B remains DISCONNECTED (cannot see or use User A's Portal session)
    status_b = client.get("/api/v1/integrations/portal/status", headers=auth_headers(user_b))
    assert status_b.status_code == 200
    assert status_b.json()["connection_status"] == "DISCONNECTED"

    # User A triggers manual sync via API
    sync_resp = client.post(
        "/api/v1/integrations/portal/sync",
        headers=auth_headers(user_a),
        json={"start_date": "2026-12-07", "end_date": "2026-12-13"},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["status"] == "SUCCESS"
    assert sync_resp.json()["items_added"] == 2
