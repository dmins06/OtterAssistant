from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReminderTargetType


class ReminderCreate(BaseModel):
    target_type: ReminderTargetType = ReminderTargetType.CUSTOM
    target_id: int | None = None
    title: str = Field(..., min_length=1, max_length=255)
    notes: str | None = None
    remind_at: datetime
    is_enabled: bool = True


class ReminderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = None
    remind_at: datetime | None = None
    is_enabled: bool | None = None


class ReminderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: ReminderTargetType
    target_id: int | None
    title: str
    notes: str | None
    remind_at: datetime
    is_enabled: bool
    is_sent: bool
    created_at: datetime
    updated_at: datetime
