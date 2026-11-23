from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConnectionStatus, SyncSource, SyncStatus, SyncType


class PortalSessionConnectRequest(BaseModel):
    """Connect or re-authenticate Portal UTH using a verified session JWT (never plaintext password)."""

    access_token: str = Field(..., min_length=10)
    refresh_token: str | None = Field(default=None, min_length=10)
    csrf_token: str | None = Field(default=None)


class PortalStatusResponse(BaseModel):
    """Safe Dashboard status payload — NEVER exposes JWT or credentials."""

    provider: str
    connection_status: ConnectionStatus
    is_mock: bool
    supports_refresh: bool
    access_token_expires_at: datetime | None
    last_authenticated_at: datetime | None
    last_refreshed_at: datetime | None
    last_successful_sync_at: datetime | None
    last_error_code: str | None


class PortalSyncRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class PortalSyncResponse(BaseModel):
    sync_log_id: int
    status: SyncStatus
    is_mock: bool
    items_added: int
    items_updated: int
    items_removed: int
    error_code: str | None = None
    error_message: str | None = None
    weeks_total: int = 1
    weeks_succeeded: int = 0
    weeks_failed: int = 0
    reauth_required: bool = False


class SyncLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: SyncSource
    sync_type: SyncType
    status: SyncStatus
    items_added: int
    items_updated: int
    items_removed: int
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
