from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.security import SecretEncryptor
from app.database.session import get_db
from app.integrations.lms.assignment_sync import LMSAssignmentSyncService
from app.integrations.lms.authentication import LMSAuthManager
from app.integrations.lms.client import (
    ICalFeedLMSClient,
    LMSProvider,
    MockLMSClient,
    MoodleWebServiceClient,
)
from app.models.academic import LMSCourse
from app.models.connection import SyncLog
from app.models.enums import SyncSource, SyncType
from app.models.user import User
from app.schemas.lms import (
    LMSConnectRequest,
    LMSCourseResponse,
    LMSStatusResponse,
    LMSSyncResponse,
)
from app.schemas.portal import SyncLogResponse

router = APIRouter(prefix="/integrations/lms", tags=["lms-integration"])


def get_lms_auth_manager(db: Session = Depends(get_db)) -> LMSAuthManager:
    return LMSAuthManager(db=db, encryptor=SecretEncryptor())


def resolve_lms_provider(method: str) -> LMSProvider:
    if method == "moodle_ws":
        return MoodleWebServiceClient()
    if method == "ical_feed":
        return ICalFeedLMSClient()
    return MockLMSClient()


@router.get("/status", response_model=LMSStatusResponse)
def get_lms_status(
    current_user: User = Depends(get_current_user),
    auth_manager: LMSAuthManager = Depends(get_lms_auth_manager),
) -> LMSStatusResponse:
    snap = auth_manager.get_status(current_user.id)
    return LMSStatusResponse(**snap.__dict__)


@router.post("/connect", response_model=LMSStatusResponse)
def connect_lms(
    payload: LMSConnectRequest,
    current_user: User = Depends(get_current_user),
    auth_manager: LMSAuthManager = Depends(get_lms_auth_manager),
) -> LMSStatusResponse:
    try:
        snap = auth_manager.connect(
            user_id=current_user.id,
            integration_method=payload.integration_method,
            wstoken=payload.wstoken,
            ical_url=payload.ical_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LMSStatusResponse(**snap.__dict__)


@router.delete("/connect", response_model=LMSStatusResponse)
def disconnect_lms(
    current_user: User = Depends(get_current_user),
    auth_manager: LMSAuthManager = Depends(get_lms_auth_manager),
) -> LMSStatusResponse:
    snap = auth_manager.disconnect(current_user.id)
    return LMSStatusResponse(**snap.__dict__)


@router.post("/sync", response_model=LMSSyncResponse)
def sync_lms_assignments(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    auth_manager: LMSAuthManager = Depends(get_lms_auth_manager),
) -> LMSSyncResponse:
    snap = auth_manager.get_status(current_user.id)
    provider = resolve_lms_provider(snap.integration_method)
    sync_service = LMSAssignmentSyncService(db=db, provider=provider, auth_manager=auth_manager)
    result = sync_service.sync_assignments(user_id=current_user.id, sync_type=SyncType.MANUAL)
    return LMSSyncResponse(**result.__dict__)


@router.get("/courses", response_model=list[LMSCourseResponse])
def list_lms_courses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LMSCourseResponse]:
    stmt = (
        select(LMSCourse)
        .where(LMSCourse.user_id == current_user.id)
        .order_by(LMSCourse.course_name.asc())
    )
    courses = list(db.scalars(stmt).all())
    return [LMSCourseResponse.model_validate(c) for c in courses]


@router.get("/sync-logs", response_model=list[SyncLogResponse])
def list_lms_sync_logs(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLogResponse]:
    stmt = (
        select(SyncLog)
        .where(SyncLog.user_id == current_user.id, SyncLog.source == SyncSource.LMS)
        .order_by(SyncLog.started_at.desc())
        .limit(limit)
    )
    logs = list(db.scalars(stmt).all())
    return [SyncLogResponse.model_validate(log) for log in logs]
