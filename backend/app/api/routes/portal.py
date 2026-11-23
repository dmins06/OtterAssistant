from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.config import get_settings
from app.core.security import SecretEncryptor
from app.database.session import get_db
from app.integrations.portal.client import MockPortalClient, PortalProvider, UTHPortalHttpClient
from app.integrations.portal.exceptions import PortalReauthRequiredError
from app.integrations.portal.schedule_sync import PortalScheduleSyncService
from app.integrations.portal.session_manager import PortalSessionManager
from app.integrations.portal.token_manager import PortalTokenManager
from app.models.connection import SyncLog
from app.models.enums import SyncSource, SyncType
from app.models.user import User
from app.schemas.portal import (
    PortalSessionConnectRequest,
    PortalStatusResponse,
    PortalSyncRequest,
    PortalSyncResponse,
    SyncLogResponse,
)

router = APIRouter(prefix="/integrations/portal", tags=["portal-integration"])


def get_portal_provider() -> PortalProvider:
    settings = get_settings()
    if settings.portal_use_mock:
        return MockPortalClient()
    return UTHPortalHttpClient()


def get_portal_session_manager(
    db: Session = Depends(get_db),
    provider: PortalProvider = Depends(get_portal_provider),
) -> PortalSessionManager:
    token_manager = PortalTokenManager(SecretEncryptor())
    return PortalSessionManager(db=db, provider=provider, token_manager=token_manager)


@router.get("/status", response_model=PortalStatusResponse)
def get_portal_status(
    current_user: User = Depends(get_current_user),
    manager: PortalSessionManager = Depends(get_portal_session_manager),
) -> PortalStatusResponse:
    snap = manager.get_status(current_user.id)
    return PortalStatusResponse(**snap.__dict__)


@router.post("/session", response_model=PortalStatusResponse)
def connect_portal_session(
    payload: PortalSessionConnectRequest,
    current_user: User = Depends(get_current_user),
    manager: PortalSessionManager = Depends(get_portal_session_manager),
) -> PortalStatusResponse:
    try:
        snap = manager.connect_with_token(
            user_id=current_user.id,
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            csrf_token=payload.csrf_token,
        )
    except (ValueError, PortalReauthRequiredError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PortalStatusResponse(**snap.__dict__)


@router.delete("/session", response_model=PortalStatusResponse)
def disconnect_portal_session(
    current_user: User = Depends(get_current_user),
    manager: PortalSessionManager = Depends(get_portal_session_manager),
) -> PortalStatusResponse:
    snap = manager.disconnect(current_user.id)
    return PortalStatusResponse(**snap.__dict__)


@router.post("/sync", response_model=PortalSyncResponse)
def sync_portal_schedule(
    payload: PortalSyncRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: PortalProvider = Depends(get_portal_provider),
    manager: PortalSessionManager = Depends(get_portal_session_manager),
) -> PortalSyncResponse:
    req = payload or PortalSyncRequest()
    sync_service = PortalScheduleSyncService(db=db, provider=provider, session_manager=manager)
    result = sync_service.sync_schedules(
        user_id=current_user.id,
        start_date=req.start_date,
        end_date=req.end_date,
        sync_type=SyncType.MANUAL,
    )
    return PortalSyncResponse(**result.__dict__)


@router.get("/sync-logs", response_model=list[SyncLogResponse])
def list_portal_sync_logs(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLogResponse]:
    stmt = (
        select(SyncLog)
        .where(SyncLog.user_id == current_user.id, SyncLog.source == SyncSource.PORTAL)
        .order_by(SyncLog.started_at.desc())
        .limit(limit)
    )
    logs = list(db.scalars(stmt).all())
    return [SyncLogResponse.model_validate(log) for log in logs]
