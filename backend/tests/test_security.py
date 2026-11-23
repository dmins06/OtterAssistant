from cryptography.fernet import Fernet
import pytest

from app.core.logging import redact_sensitive
from app.core.security import (
    SecretEncryptor,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing_and_verification() -> None:
    raw = "StrongStudentPass!2026"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_create_and_decode() -> None:
    token = create_access_token(subject="user-123", extra_claims={"role": "student"})
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "student"
    assert "exp" in payload and "iat" in payload


def test_secret_encryptor_roundtrip_and_invalid_key() -> None:
    key = Fernet.generate_key().decode("utf-8")
    encryptor = SecretEncryptor(key=key)
    secret_value = "portal-refresh-token-value"
    encrypted = encryptor.encrypt(secret_value)
    assert encrypted != secret_value
    assert encryptor.decrypt(encrypted) == secret_value

    other_encryptor = SecretEncryptor(key=Fernet.generate_key().decode("utf-8"))
    with pytest.raises(ValueError):
        other_encryptor.decrypt(encrypted)


def test_log_redaction_strips_jwt_and_secrets() -> None:
    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart123"
    raw_log = f'Authorization: Bearer {fake_jwt} password="mysecretpassword" api_key=AIzaSyTestKey123'
    redacted = redact_sensitive(raw_log)
    assert fake_jwt not in redacted
    assert "mysecretpassword" not in redacted
    assert "AIzaSyTestKey123" not in redacted
    assert "[REDACTED_JWT]" in redacted
    assert "[REDACTED]" in redacted
