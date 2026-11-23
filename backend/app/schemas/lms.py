from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConnectionStatus, CourseMappingStatus, SyncStatus


class LMSConnectRequest(BaseModel):
    """Connect UTH LMS via an authorized method (moodle_ws, ical_feed, or mock)."""

    integration_method: str = Field(..., description="moodle_ws | ical_feed | mock")
    wstoken: str | None = Field(default=None)
    ical_url: str | None = Field(default=None)


class LMSStatusResponse(BaseModel):
    """Safe LMS status payload — NEVER exposes wstoken or private iCal URL."""

    provider: str
    integration_method: str
    connection_status: ConnectionStatus
    is_mock: bool
    last_authenticated_at: datetime | None
    last_successful_sync_at: datetime | None
    last_error_code: str | None


class LMSCourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int | None
    external_course_id: str
    course_code: str | None
    course_name: str
    course_url: str | None
    mapping_status: CourseMappingStatus
    last_synced_at: datetime | None


class LMSSyncResponse(BaseModel):
    sync_log_id: int
    status: SyncStatus
    is_mock: bool
    courses_synced: int
    items_added: int
    items_updated: int
    items_removed: int
    deadlines_changed: int
    error_code: str | None = None
    error_message: str | None = None
