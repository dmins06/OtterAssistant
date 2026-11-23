from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_health_check() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Otter Assistant"
    assert body["timezone"] == "Asia/Ho_Chi_Minh"
    assert "timestamp_utc" in body


def test_api_v1_liveness_health_check() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_health_check_ready() -> None:
    client = TestClient(create_app())
    with (
        patch("app.api.routes.health.check_database_health", return_value=(True, "ok")),
        patch("app.api.routes.health.check_redis_health", return_value=(True, "ok")),
    ):
        response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"mysql": "ok", "redis": "ok"}


def test_readiness_health_check_degraded_returns_503() -> None:
    client = TestClient(create_app())
    with (
        patch("app.api.routes.health.check_database_health", return_value=(False, "unavailable: OperationalError")),
        patch("app.api.routes.health.check_redis_health", return_value=(True, "ok")),
    ):
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["mysql"].startswith("unavailable")
