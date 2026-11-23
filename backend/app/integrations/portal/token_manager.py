from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

import jwt

from app.core.security import SecretEncryptor
from app.core.timezone import utc_now


@dataclass(frozen=True)
class PortalTokenBundle:
    access_token: str
    refresh_token: str | None = None
    csrf_token: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "csrf_token": self.csrf_token,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "PortalTokenBundle":
        data = json.loads(raw)
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            csrf_token=data.get("csrf_token"),
        )


class PortalTokenManager:
    """
    Handles Portal JWT inspection and encrypted persistence.
    Never logs raw JWTs or refresh tokens.
    """

    def __init__(self, encryptor: SecretEncryptor) -> None:
        self._encryptor = encryptor

    @staticmethod
    def extract_expiration(access_token: str) -> datetime | None:
        """
        Decode JWT claims locally without signature verification to read `exp`.
        Returns UTC datetime if `exp` claim exists, else None.
        """
        try:
            claims = jwt.decode(
                access_token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError:
            return None

        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        return None

    @staticmethod
    def is_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
        if expires_at is None:
            return False
        current = now or utc_now()
        exp_utc = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(timezone.utc)
        return current >= exp_utc

    @staticmethod
    def is_expiring_soon(
        expires_at: datetime | None,
        buffer_seconds: int = 300,
        now: datetime | None = None,
    ) -> bool:
        if expires_at is None:
            return False
        current = now or utc_now()
        exp_utc = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(timezone.utc)
        return current + timedelta(seconds=buffer_seconds) >= exp_utc

    def encrypt_bundle(self, bundle: PortalTokenBundle) -> str:
        return self._encryptor.encrypt(bundle.to_json())

    def decrypt_bundle(self, encrypted_payload: str) -> PortalTokenBundle:
        return PortalTokenBundle.from_json(self._encryptor.decrypt(encrypted_payload))
