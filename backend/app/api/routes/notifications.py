from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.database.session import get_db
from app.models.enums import NotificationStatus, NotificationType
from app.models.notification import NotificationLog
from app.models.user import User, UserPreference

router = APIRouter(tags=["notifications-and-preferences"])


class NotificationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_type: NotificationType
    channel: str
    status: NotificationStatus
    scheduled_for: datetime
    sent_at: datetime | None
    payload_summary: str | None
    error_detail: str | None


class UserPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: str
    class_reminder_minutes: int
    assignment_reminder_days: str
    daily_summary_enabled: bool
    daily_summary_time: str
    weekly_summary_enabled: bool
    weekly_summary_day: int


class UserPreferenceUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    class_reminder_minutes: int | None = Field(default=None, ge=5, le=180)
    assignment_reminder_days: str | None = Field(default=None, max_length=64)
    daily_summary_enabled: bool | None = None
    daily_summary_time: str | None = Field(default=None, max_length=8)
    weekly_summary_enabled: bool | None = None
    weekly_summary_day: int | None = Field(default=None, ge=0, le=6)


@router.get("/notifications/logs", response_model=list[NotificationLogResponse])
def list_notification_logs(
    limit: int = Query(default=30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NotificationLogResponse]:
    stmt = (
        select(NotificationLog)
        .where(NotificationLog.user_id == current_user.id)
        .order_by(NotificationLog.created_at.desc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    return [NotificationLogResponse.model_validate(r) for r in rows]


@router.get("/preferences", response_model=UserPreferenceResponse)
def get_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPreferenceResponse:
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == current_user.id))
    if pref is None:
        pref = UserPreference(user_id=current_user.id)
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return UserPreferenceResponse.model_validate(pref)


@router.patch("/preferences", response_model=UserPreferenceResponse)
def update_preferences(
    payload: UserPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPreferenceResponse:
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == current_user.id))
    if pref is None:
        pref = UserPreference(user_id=current_user.id)
        db.add(pref)
        db.flush()

    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(pref, field_name, value)
    db.commit()
    db.refresh(pref)
    return UserPreferenceResponse.model_validate(pref)
