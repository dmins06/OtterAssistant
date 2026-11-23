from dataclasses import dataclass
from datetime import datetime
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SecretEncryptor
from app.core.timezone import utc_now
from app.integrations.lms.exceptions import LMSAuthRequiredError
from app.models.connection import LMSConnection
from app.models.enums import ConnectionStatus

logger = logging.getLogger(__name__)

SUPPORTED_LMS_METHODS = {"moodle_ws", "ical_feed", "mock"}


@dataclass(frozen=True)
class LMSCredentialBundle:
    integration_method: str
    wstoken: str | None = None
    ical_url: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "integration_method": self.integration_method,
                "wstoken": self.wstoken,
                "ical_url": self.ical_url,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "LMSCredentialBundle":
        data = json.loads(raw)
        return cls(
            integration_method=data["integration_method"],
            wstoken=data.get("wstoken"),
            ical_url=data.get("ical_url"),
        )


@dataclass(frozen=True)
class LMSStatusSnapshot:
    provider: str
    integration_method: str
    connection_status: ConnectionStatus
    is_mock: bool
    last_authenticated_at: datetime | None
    last_successful_sync_at: datetime | None
    last_error_code: str | None


class LMSAuthManager:
    """
    Dedicated authentication manager for UTH LMS — completely decoupled from Portal JWT.
    Encrypts wstoken / private iCal URL at rest using Fernet; never exposes credentials.
    """

    def __init__(self, db: Session, encryptor: SecretEncryptor) -> None:
        self.db = db
        self._encryptor = encryptor

    def _get_or_create_connection(self, user_id: int) -> LMSConnection:
        conn = self.db.scalar(
            select(LMSConnection).where(LMSConnection.user_id == user_id)
        )
        if conn is None:
            conn = LMSConnection(
                user_id=user_id,
                provider="uth_lms",
                integration_method="unconfigured",
                connection_status=ConnectionStatus.DISCONNECTED,
            )
            self.db.add(conn)
            self.db.flush()
        return conn

    def get_status(self, user_id: int) -> LMSStatusSnapshot:
        conn = self._get_or_create_connection(user_id)
        return LMSStatusSnapshot(
            provider=conn.provider,
            integration_method=conn.integration_method,
            connection_status=conn.connection_status,
            is_mock=conn.integration_method == "mock",
            last_authenticated_at=conn.last_authenticated_at,
            last_successful_sync_at=conn.last_successful_sync_at,
            last_error_code=conn.last_error_code,
        )

    def connect(
        self,
        user_id: int,
        integration_method: str,
        wstoken: str | None = None,
        ical_url: str | None = None,
    ) -> LMSStatusSnapshot:
        method = integration_method.strip().lower()
        if method not in SUPPORTED_LMS_METHODS:
            raise ValueError(f"Unsupported LMS integration_method: {method}")
        if method == "moodle_ws" and (not wstoken or not wstoken.strip()):
            raise ValueError("wstoken is required for moodle_ws integration.")
        if method == "ical_feed" and (not ical_url or not ical_url.strip().startswith(("http://", "https://"))):
            raise ValueError("A valid http(s) ical_url is required for ical_feed integration.")

        bundle = LMSCredentialBundle(
            integration_method=method,
            wstoken=wstoken.strip() if wstoken else None,
            ical_url=ical_url.strip() if ical_url else None,
        )
        now = utc_now()
        conn = self._get_or_create_connection(user_id)
        conn.integration_method = method
        conn.encrypted_credentials = self._encryptor.encrypt(bundle.to_json())
        conn.connection_status = ConnectionStatus.AUTHENTICATED
        conn.last_authenticated_at = now
        conn.last_error_code = None
        self.db.commit()
        self.db.refresh(conn)
        logger.info("LMS connected for user_id=%s method=%s", user_id, method)
        return self.get_status(user_id)

    def disconnect(self, user_id: int) -> LMSStatusSnapshot:
        conn = self._get_or_create_connection(user_id)
        conn.integration_method = "unconfigured"
        conn.encrypted_credentials = None
        conn.connection_status = ConnectionStatus.DISCONNECTED
        conn.last_error_code = None
        self.db.commit()
        self.db.refresh(conn)
        logger.info("LMS disconnected for user_id=%s", user_id)
        return self.get_status(user_id)

    def get_credentials(self, user_id: int) -> LMSCredentialBundle:
        conn = self._get_or_create_connection(user_id)
        if conn.connection_status == ConnectionStatus.DISCONNECTED or not conn.encrypted_credentials:
            raise LMSAuthRequiredError("UTH LMS is not connected.", error_code="LMS_DISCONNECTED")
        try:
            return LMSCredentialBundle.from_json(self._encryptor.decrypt(conn.encrypted_credentials))
        except ValueError as exc:
            conn.connection_status = ConnectionStatus.REAUTH_REQUIRED
            conn.last_error_code = "LMS_DECRYPTION_FAILED"
            self.db.commit()
            raise LMSAuthRequiredError(
                "Stored LMS credentials could not be decrypted.",
                error_code="LMS_DECRYPTION_FAILED",
            ) from exc

    def mark_sync_success(self, user_id: int) -> None:
        conn = self._get_or_create_connection(user_id)
        conn.connection_status = ConnectionStatus.AUTHENTICATED
        conn.last_successful_sync_at = utc_now()
        conn.last_error_code = None
        self.db.commit()

    def mark_error(self, user_id: int, error_code: str, require_reauth: bool = False) -> None:
        conn = self._get_or_create_connection(user_id)
        conn.connection_status = (
            ConnectionStatus.REAUTH_REQUIRED if require_reauth else ConnectionStatus.ERROR
        )
        conn.last_error_code = error_code
        self.db.commit()
