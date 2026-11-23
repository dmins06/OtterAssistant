from datetime import datetime, timezone

import redis
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.database.session import check_database_health

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    timezone: str
    timestamp_utc: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]
    timestamp_utc: str


def check_redis_health() -> tuple[bool, str]:
    settings = get_settings()
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        )
        client.ping()
        client.close()
        return True, "ok"
    except Exception as exc:  # pragma: no cover - depends on external Redis
        return False, f"unavailable: {type(exc).__name__}"


@router.get("/health", response_model=HealthResponse)
def liveness_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        timezone=settings.app_timezone,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/health/ready", response_model=ReadinessResponse)
def readiness_check() -> JSONResponse:
    db_ok, db_detail = check_database_health()
    redis_ok, redis_detail = check_redis_health()
    all_ok = db_ok and redis_ok

    payload = ReadinessResponse(
        status="ready" if all_ok else "degraded",
        checks={
            "mysql": db_detail,
            "redis": redis_detail,
        },
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )
