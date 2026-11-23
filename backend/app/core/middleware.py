from collections import defaultdict, deque
import logging
import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings

logger = logging.getLogger(__name__)


def validate_production_settings(settings: Settings) -> None:
    """Fail fast on insecure configuration when running in APP_ENV=production."""
    if not settings.is_production:
        return
    if not settings.secret_key or settings.secret_key.startswith("dev-only") or "replace-with" in settings.secret_key:
        raise RuntimeError("SECRET_KEY must be set to a strong random secret in production.")
    if not settings.encryption_key or "replace-with" in settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY must be set to a valid Fernet key in production.")
    if settings.app_debug:
        raise RuntimeError("APP_DEBUG must be false in production.")


class SecurityHeadersAndLatencyMiddleware(BaseHTTPMiddleware):
    """Attach standard HTTP security headers and structured API latency logging."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"

        logger.info(
            "HTTP %s %s status=%d latency_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


class SlidingWindowRateLimiter:
    """Lightweight in-memory sliding window rate limiter for sensitive endpoints."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        ts = now if now is not None else time.monotonic()
        cutoff = ts - self.window_seconds
        bucket = self._hits[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (ts - bucket[0])))
            return False, retry_after
        bucket.append(ts)
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limiting to authentication and AI chat routes."""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.limiter = SlidingWindowRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        path = request.url.path
        if path.startswith("/api/v1/auth/") or path.startswith("/api/v1/agent/"):
            client_ip = request.client.host if request.client else "unknown"
            allowed, retry_after = self.limiter.check(f"{client_ip}:{path}")
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Please try again shortly."},
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
