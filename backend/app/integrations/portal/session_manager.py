from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import time as time_module
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.integrations.portal.client import PortalProvider
from app.integrations.portal.exceptions import (
    PortalHTTPStatusError,
    PortalIntegrationError,
    PortalReauthRequiredError,
)
from app.integrations.portal.token_manager import PortalTokenBundle, PortalTokenManager
from app.models.connection import PortalConnection
from app.models.enums import ConnectionStatus

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class PortalStatusSnapshot:
    provider: str
    connection_status: ConnectionStatus
    is_mock: bool
    supports_refresh: bool
    access_token_expires_at: datetime | None
    last_authenticated_at: datetime | None
    last_refreshed_at: datetime | None
    last_successful_sync_at: datetime | None
    last_error_code: str | None


class PortalSessionManager:
    """
    Manages Portal UTH JWT lifecycle and HTTP resilience per Section 5:
      - Tracks connection status (7 states)
      - Detects expiring/expired JWT locally via `exp`
      - Proactively refreshes when official refresh endpoint is verified
      - Handles HTTP 401 (refresh + single retry), 403 (CSRF vs permission), 429 (Retry-After), 5xx (bounded retry)
      - Never logs raw tokens or returns credentials to callers/frontend
    """

    def __init__(
        self,
        db: Session,
        provider: PortalProvider,
        token_manager: PortalTokenManager,
        refresh_buffer_seconds: int | None = None,
        max_5xx_retries: int = 2,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self.db = db
        self.provider = provider
        self.token_manager = token_manager
        self.refresh_buffer_seconds = (
            refresh_buffer_seconds
            if refresh_buffer_seconds is not None
            else get_settings().portal_token_refresh_buffer_seconds
        )
        self.max_5xx_retries = max_5xx_retries
        self._sleep = sleep_fn

    def _get_or_create_connection(self, user_id: int) -> PortalConnection:
        conn = self.db.scalar(
            select(PortalConnection).where(PortalConnection.user_id == user_id)
        )
        if conn is None:
            conn = PortalConnection(
                user_id=user_id,
                provider="uth_portal",
                connection_status=ConnectionStatus.DISCONNECTED,
            )
            self.db.add(conn)
            self.db.flush()
        return conn

    def get_status(self, user_id: int) -> PortalStatusSnapshot:
        conn = self._get_or_create_connection(user_id)
        effective_status = conn.connection_status
        if effective_status == ConnectionStatus.AUTHENTICATED and conn.access_token_expires_at:
            if self.token_manager.is_expired(conn.access_token_expires_at):
                effective_status = ConnectionStatus.SESSION_EXPIRED
            elif self.token_manager.is_expiring_soon(
                conn.access_token_expires_at, self.refresh_buffer_seconds
            ):
                effective_status = ConnectionStatus.TOKEN_EXPIRING

        return PortalStatusSnapshot(
            provider=conn.provider,
            connection_status=effective_status,
            is_mock=self.provider.is_mock,
            supports_refresh=self.provider.supports_refresh,
            access_token_expires_at=conn.access_token_expires_at,
            last_authenticated_at=conn.last_authenticated_at,
            last_refreshed_at=conn.last_refreshed_at,
            last_successful_sync_at=conn.last_successful_sync_at,
            last_error_code=conn.last_error_code,
        )

    def connect_with_token(
        self,
        user_id: int,
        access_token: str,
        refresh_token: str | None = None,
        csrf_token: str | None = None,
    ) -> PortalStatusSnapshot:
        """Store an encrypted Portal session token bundle without storing Portal passwords."""
        if not access_token or not access_token.strip():
            raise ValueError("Access token must not be empty.")

        now = utc_now()
        expires_at = self.token_manager.extract_expiration(access_token.strip())
        if expires_at is not None and self.token_manager.is_expired(expires_at, now=now):
            raise PortalReauthRequiredError(
                "Provided Portal JWT is already expired.",
                error_code="TOKEN_ALREADY_EXPIRED",
            )

        bundle = PortalTokenBundle(
            access_token=access_token.strip(),
            refresh_token=refresh_token.strip() if refresh_token else None,
            csrf_token=csrf_token.strip() if csrf_token else None,
        )
        conn = self._get_or_create_connection(user_id)
        conn.encrypted_credentials = self.token_manager.encrypt_bundle(bundle)
        conn.access_token_expires_at = expires_at
        conn.connection_status = ConnectionStatus.AUTHENTICATED
        conn.last_authenticated_at = now
        conn.last_error_code = None
        self.db.commit()
        self.db.refresh(conn)
        logger.info("Portal session authenticated for user_id=%s", user_id)
        return self.get_status(user_id)

    def disconnect(self, user_id: int) -> PortalStatusSnapshot:
        conn = self._get_or_create_connection(user_id)
        conn.encrypted_credentials = None
        conn.access_token_expires_at = None
        conn.connection_status = ConnectionStatus.DISCONNECTED
        conn.last_error_code = None
        self.db.commit()
        self.db.refresh(conn)
        logger.info("Portal session disconnected for user_id=%s", user_id)
        return self.get_status(user_id)

    def mark_sync_success(self, user_id: int) -> None:
        conn = self._get_or_create_connection(user_id)
        conn.last_successful_sync_at = utc_now()
        conn.last_error_code = None
        self.db.commit()

    def _mark_reauth_required(self, conn: PortalConnection, error_code: str) -> None:
        conn.connection_status = ConnectionStatus.REAUTH_REQUIRED
        conn.last_error_code = error_code
        self.db.commit()

    def _try_refresh(self, conn: PortalConnection, bundle: PortalTokenBundle) -> PortalTokenBundle:
        if not self.provider.supports_refresh or not bundle.refresh_token:
            self._mark_reauth_required(conn, "REFRESH_UNAVAILABLE")
            raise PortalReauthRequiredError(
                "Portal session expired and automatic refresh is not available.",
                error_code="REFRESH_UNAVAILABLE",
            )

        conn.connection_status = ConnectionStatus.REFRESHING
        self.db.commit()

        try:
            refreshed = self.provider.refresh_access_token(bundle.refresh_token)
        except PortalIntegrationError as exc:
            self._mark_reauth_required(conn, exc.error_code)
            raise PortalReauthRequiredError(
                "Portal token refresh failed; user must re-authenticate.",
                error_code=exc.error_code,
            ) from exc

        now = utc_now()
        new_exp = self.token_manager.extract_expiration(refreshed.access_token)
        conn.encrypted_credentials = self.token_manager.encrypt_bundle(refreshed)
        conn.access_token_expires_at = new_exp
        conn.connection_status = ConnectionStatus.AUTHENTICATED
        conn.last_refreshed_at = now
        conn.last_error_code = None
        self.db.commit()
        logger.info("Portal token refreshed successfully for user_id=%s", conn.user_id)
        return refreshed

    def ensure_valid_bundle(self, user_id: int) -> PortalTokenBundle:
        conn = self._get_or_create_connection(user_id)
        if conn.connection_status == ConnectionStatus.DISCONNECTED or not conn.encrypted_credentials:
            raise PortalReauthRequiredError(
                "Portal UTH is not connected.",
                error_code="DISCONNECTED",
            )

        try:
            bundle = self.token_manager.decrypt_bundle(conn.encrypted_credentials)
        except ValueError as exc:
            self._mark_reauth_required(conn, "DECRYPTION_FAILED")
            raise PortalReauthRequiredError(
                "Stored Portal session could not be decrypted; please re-authenticate.",
                error_code="DECRYPTION_FAILED",
            ) from exc

        now = utc_now()
        exp = conn.access_token_expires_at or self.token_manager.extract_expiration(bundle.access_token)
        if exp is not None:
            if self.token_manager.is_expired(exp, now=now):
                conn.connection_status = ConnectionStatus.SESSION_EXPIRED
                self.db.commit()
                return self._try_refresh(conn, bundle)
            if self.token_manager.is_expiring_soon(exp, self.refresh_buffer_seconds, now=now):
                conn.connection_status = ConnectionStatus.TOKEN_EXPIRING
                self.db.commit()
                if self.provider.supports_refresh and bundle.refresh_token:
                    return self._try_refresh(conn, bundle)

        return bundle

    def execute_authenticated(
        self,
        user_id: int,
        request_fn: Callable[[PortalTokenBundle], T],
    ) -> T:
        """
        Execute a Portal API call with compliant HTTP 401/403/429/5xx handling.
        """
        bundle = self.ensure_valid_bundle(user_id)
        conn = self._get_or_create_connection(user_id)
        retried_401 = False
        retries_5xx = 0
        retries_429 = 0

        while True:
            try:
                return request_fn(bundle)
            except PortalHTTPStatusError as exc:
                if exc.status_code == 401:
                    if not retried_401:
                        retried_401 = True
                        bundle = self._try_refresh(conn, bundle)
                        continue
                    self._mark_reauth_required(conn, "PORTAL_HTTP_401")
                    raise PortalReauthRequiredError(
                        "Portal rejected credentials after refresh retry.",
                        error_code="PORTAL_HTTP_401",
                    ) from exc

                if exc.status_code == 403:
                    # Distinguish CSRF / permission error — do NOT blindly treat as expired JWT
                    conn.connection_status = ConnectionStatus.ERROR
                    conn.last_error_code = "PORTAL_CSRF_ERROR" if exc.is_csrf_error else "PORTAL_FORBIDDEN"
                    self.db.commit()
                    raise

                if exc.status_code == 429 and retries_429 < 2:
                    retries_429 += 1
                    delay = min(exc.retry_after_seconds or float(2**retries_429), 30.0)
                    logger.warning(
                        "Portal rate limited (429) for user_id=%s; backing off %.1fs",
                        user_id,
                        delay,
                    )
                    self._sleep(delay)
                    continue

                if 500 <= exc.status_code < 600 and retries_5xx < self.max_5xx_retries:
                    retries_5xx += 1
                    delay = float(2 ** (retries_5xx - 1))
                    logger.warning(
                        "Portal server error (%s) for user_id=%s; retry %s/%s",
                        exc.status_code,
                        user_id,
                        retries_5xx,
                        self.max_5xx_retries,
                    )
                    self._sleep(delay)
                    continue

                conn.last_error_code = exc.error_code
                self.db.commit()
                raise
