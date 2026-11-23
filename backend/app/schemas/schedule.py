from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import SessionPeriod


class ScheduleUpsert(BaseModel):
    subject_id: int
    source_event_id: str = Field(..., min_length=1, max_length=128)
    class_date: date
    weekday: int = Field(..., ge=2, le=8)
    session: SessionPeriod
    class_session: str = Field(..., min_length=1, max_length=32)
    period_start: int = Field(..., ge=1, le=18)
    period_end: int = Field(..., ge=1, le=18)
    start_time: time
    end_time: time
    room: str | None = Field(default=None, max_length=128)
    campus: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=255)
    raw_room_text: str | None = Field(default=None, max_length=512)
    syllabus_url: str | None = Field(default=None, max_length=1024)
    lms_url: str | None = Field(default=None, max_length=1024)
    is_deleted: bool = False

    @model_validator(mode="after")
    def validate_ranges(self) -> "ScheduleUpsert":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    subject_code: str | None = None
    subject_name: str | None = None
    source_event_id: str
    class_date: date
    weekday: int
    session: SessionPeriod
    class_session: str
    period_start: int
    period_end: int
    start_time: time
    end_time: time
    room: str | None
    campus: str | None
    location: str | None
    raw_room_text: str | None
    syllabus_url: str | None
    lms_url: str | None
    last_synced_at: datetime | None


class ScheduleConflict(BaseModel):
    class_date: date
    first_schedule_id: int
    second_schedule_id: int
    first_subject_name: str | None
    second_subject_name: str | None
    overlap_start: time
    overlap_end: time
