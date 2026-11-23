from dataclasses import dataclass
from datetime import date, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import local_today, utc_now
from app.integrations.portal.client import PortalProvider
from app.integrations.portal.exceptions import PortalIntegrationError
from app.integrations.portal.parser import sanitize_lms_url
from app.integrations.portal.session_manager import PortalSessionManager
from app.models.academic import Schedule
from app.models.connection import SyncLog
from app.models.enums import SyncSource, SyncStatus, SyncType
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortalSyncResult:
    sync_log_id: int
    status: SyncStatus
    is_mock: bool
    items_added: int
    items_updated: int
    items_removed: int
    error_code: str | None = None
    error_message: str | None = None


class PortalScheduleSyncService:
    """
    Synchronizes UTH Portal subjects & schedules into MySQL via PortalSessionManager.
    Never wipes previously synced schedules on network/auth/5xx failures or outside the returned date range.
    """

    def __init__(
        self,
        db: Session,
        provider: PortalProvider,
        session_manager: PortalSessionManager,
    ) -> None:
        self.db = db
        self.provider = provider
        self.session_manager = session_manager
        self.subject_service = SubjectService(db)
        self.schedule_service = ScheduleService(db)

    def sync_schedules(
        self,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        sync_type: SyncType = SyncType.MANUAL,
    ) -> PortalSyncResult:
        ref = local_today()
        range_start = start_date or (ref - timedelta(days=ref.weekday()))
        range_end = end_date or (range_start + timedelta(days=6))

        started_at = utc_now()
        sync_log = SyncLog(
            user_id=user_id,
            source=SyncSource.PORTAL,
            sync_type=sync_type,
            status=SyncStatus.RUNNING,
            started_at=started_at,
        )
        self.db.add(sync_log)
        self.db.commit()
        self.db.refresh(sync_log)

        try:
            raw_items = self.session_manager.execute_authenticated(
                user_id,
                lambda bundle: self.provider.fetch_weekly_schedule(
                    access_token=bundle.access_token,
                    start_date=range_start,
                    end_date=range_end,
                    csrf_token=bundle.csrf_token,
                ),
            )
        except PortalIntegrationError as exc:
            sync_log.status = SyncStatus.FAILED
            sync_log.error_code = exc.error_code
            sync_log.error_message = str(exc)
            sync_log.finished_at = utc_now()
            self.db.commit()
            logger.warning(
                "Portal sync failed for user_id=%s error_code=%s",
                user_id,
                exc.error_code,
            )
            return PortalSyncResult(
                sync_log_id=sync_log.id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=exc.error_code,
                error_message=str(exc),
            )

        # Deduplicate incoming items by source_event_id before persistence
        deduped_items = list({item.source_event_id: item for item in raw_items}.values())

        # Determine the actual returned date range so valid schedules outside it are NEVER deleted
        if deduped_items:
            returned_dates = [item.class_date for item in deduped_items]
            cleanup_start = max(range_start, min(returned_dates))
            cleanup_end = min(range_end, max(returned_dates))
            existing_in_window = (
                list(
                    self.db.scalars(
                        select(Schedule).where(
                            Schedule.user_id == user_id,
                            Schedule.is_deleted.is_(False),
                            Schedule.class_date >= cleanup_start,
                            Schedule.class_date <= cleanup_end,
                        )
                    ).all()
                )
                if cleanup_start <= cleanup_end
                else []
            )
        else:
            existing_in_window = []

        existing_event_ids = {s.source_event_id for s in existing_in_window}
        incoming_event_ids: set[str] = set()

        added = 0
        updated = 0
        for item in deduped_items:
            incoming_event_ids.add(item.source_event_id)
            safe_lms_url = sanitize_lms_url(item.lms_url)
            subject = self.subject_service.upsert_subject(
                user_id,
                SubjectUpsert(
                    subject_code=item.subject_code,
                    name=item.subject_name,
                    lecturer=item.lecturer,
                    syllabus_url=item.syllabus_url,
                    lms_url=safe_lms_url,
                ),
            )
            already_exists = (
                item.source_event_id in existing_event_ids
                or self.schedule_service.repo.get_by_source_event_id(user_id, item.source_event_id) is not None
            )
            self.schedule_service.upsert_schedule(
                user_id,
                ScheduleUpsert(
                    subject_id=subject.id,
                    source_event_id=item.source_event_id,
                    class_date=item.class_date,
                    weekday=item.weekday,
                    session=item.session,
                    class_session=item.class_session,
                    period_start=item.period_start,
                    period_end=item.period_end,
                    start_time=item.start_time,
                    end_time=item.end_time,
                    room=item.room,
                    campus=item.campus,
                    location=item.location,
                    raw_room_text=item.raw_room_text,
                    syllabus_url=item.syllabus_url,
                    lms_url=safe_lms_url,
                    is_deleted=item.is_cancelled,
                ),
            )
            if already_exists:
                updated += 1
            else:
                added += 1

        # Soft-delete schedules removed from Portal strictly within the returned date range
        removed = 0
        for old_sched in existing_in_window:
            if old_sched.source_event_id not in incoming_event_ids:
                old_sched.is_deleted = True
                removed += 1

        sync_log.status = SyncStatus.SUCCESS
        sync_log.items_added = added
        sync_log.items_updated = updated
        sync_log.items_removed = removed
        sync_log.finished_at = utc_now()
        self.db.commit()
        self.session_manager.mark_sync_success(user_id)

        return PortalSyncResult(
            sync_log_id=sync_log.id,
            status=SyncStatus.SUCCESS,
            is_mock=self.provider.is_mock,
            items_added=added,
            items_updated=updated,
            items_removed=removed,
        )
