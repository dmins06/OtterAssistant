from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SubmissionStatus


class AssignmentUpsert(BaseModel):
    lms_course_id: int
    subject_id: int | None = None
    external_assignment_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=512)
    description: str | None = None
    created_at_source: datetime | None = None
    due_at: datetime | None = None
    submission_status: SubmissionStatus = SubmissionStatus.UNKNOWN
    submitted_at: datetime | None = None
    grade: float | None = None
    feedback: str | None = None
    attachments_json: str | None = None
    assignment_url: str | None = Field(default=None, max_length=1024)


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int | None
    lms_course_id: int
    external_assignment_id: str
    title: str
    description: str | None
    created_at_source: datetime | None
    due_at: datetime | None
    submission_status: SubmissionStatus
    submitted_at: datetime | None
    grade: float | None
    feedback: str | None
    attachments_json: str | None
    assignment_url: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
