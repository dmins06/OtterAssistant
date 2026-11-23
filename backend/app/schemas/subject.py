from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubjectUpsert(BaseModel):
    subject_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    credits: int | None = Field(default=None, ge=0, le=20)
    lecturer: str | None = Field(default=None, max_length=255)
    syllabus_url: str | None = Field(default=None, max_length=1024)
    lms_url: str | None = Field(default=None, max_length=1024)


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_code: str
    name: str
    credits: int | None
    lecturer: str | None
    syllabus_url: str | None
    lms_url: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
