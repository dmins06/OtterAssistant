from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.core.middleware import SlidingWindowRateLimiter, validate_production_settings


def test_security_headers_and_latency_header_present(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "X-Process-Time-Ms" in resp.headers


def test_production_settings_guard_rejects_weak_secrets() -> None:
    # Default dev secret must fail in production
    bad_prod = Settings(app_env="production", secret_key="dev-only-change-in-production", encryption_key="")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_production_settings(bad_prod)

    # Missing encryption key must fail in production
    bad_enc = Settings(app_env="production", secret_key="strong-random-key-123456", encryption_key="")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        validate_production_settings(bad_enc)

    # APP_DEBUG=true must fail in production
    bad_debug = Settings(
        app_env="production",
        app_debug=True,
        secret_key="strong-random-key-123456",
        encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    with pytest.raises(RuntimeError, match="APP_DEBUG"):
        validate_production_settings(bad_debug)

    # Valid production settings pass cleanly
    valid_prod = Settings(
        app_env="production",
        app_debug=False,
        secret_key="strong-random-key-123456",
        encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    validate_production_settings(valid_prod)


def test_sliding_window_rate_limiter_enforces_limit_and_recovers() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=10)
    assert limiter.check("127.0.0.1:/api/v1/auth/login", now=100.0) == (True, 0)
    assert limiter.check("127.0.0.1:/api/v1/auth/login", now=101.0) == (True, 0)
    assert limiter.check("127.0.0.1:/api/v1/auth/login", now=102.0) == (True, 0)

    allowed, retry_after = limiter.check("127.0.0.1:/api/v1/auth/login", now=103.0)
    assert allowed is False
    assert retry_after >= 1

    # Window slides past 100.0 -> allowed again
    assert limiter.check("127.0.0.1:/api/v1/auth/login", now=111.0) == (True, 0)


def test_auth_register_login_and_me_flow(client: TestClient) -> None:
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "e2e.student@ut.edu.vn",
            "full_name": "Lê Hoàng Duy Minh",
            "password": "SecurePassword!2026",
            "student_code": "2251120099",
        },
    )
    assert reg.status_code == 201
    assert reg.json()["email"] == "e2e.student@ut.edu.vn"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "e2e.student@ut.edu.vn", "password": "SecurePassword!2026"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["student_code"] == "2251120099"

    # Unauthenticated request rejected with 401
    assert client.get("/api/v1/auth/me").status_code == 401
