import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.agent import router as agent_router
from app.api.routes.assignments import router as assignments_router
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.lms import router as lms_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.portal import router as portal_router
from app.api.routes.reminders import router as reminders_router
from app.api.routes.schedules import router as schedules_router
from app.api.routes.subjects import router as subjects_router
from app.api.routes.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    RateLimitMiddleware,
    SecurityHeadersAndLatencyMiddleware,
    validate_production_settings,
)
from app.scheduler.manager import create_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging("DEBUG" if settings.app_debug else "INFO")
    validate_production_settings(settings)
    logger.info(
        "Starting %s (env=%s, timezone=%s)",
        settings.app_name,
        settings.app_env,
        settings.app_timezone,
    )
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = create_scheduler()
        scheduler.start()
        logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="AI-Powered Personal Academic Assistant for UTH Students",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)
    app.add_middleware(SecurityHeadersAndLatencyMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root health routes for Docker/load-balancer probes and API v1 routes
    app.include_router(health_router)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(subjects_router, prefix="/api/v1")
    app.include_router(schedules_router, prefix="/api/v1")
    app.include_router(assignments_router, prefix="/api/v1")
    app.include_router(reminders_router, prefix="/api/v1")
    app.include_router(portal_router, prefix="/api/v1")
    app.include_router(lms_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(telegram_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")

    return app


app = create_app()
