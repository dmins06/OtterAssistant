from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
import logging
import time as time_module

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import redact_sensitive
from app.core.timezone import local_today, utc_now
from app.integrations.portal.client import PortalProvider
from app.integrations.portal.exceptions import (
    PortalIntegrationError,
    PortalReauthRequiredError,
)
from app.integrations.portal.parser import PortalScheduleItem, sanitize_lms_url
from app.integrations.portal.semester import resolve_semester_range
from app.integrations.portal.session_manager import PortalSessionManager
from app.integrations.portal.token_manager import PortalTokenBundle
from app.models.academic import Schedule
from app.models.connection import SyncLog
from app.models.enums import SyncSource, SyncStatus, SyncType
from app.scheduler.locks import LockLease, RedisDistributedLock
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
    weeks_total: int = 1
    weeks_succeeded: int = 0
    weeks_failed: int = 0
    reauth_required: bool = False


class PortalScheduleSyncService:
    """
    Synchronizes UTH Portal subjects & schedules into MySQL via PortalSessionManager.
    - Single-range `sync_schedules()` runs atomically in one transaction.
    - Multi-week `sync_semester_schedules()` runs each week in an independent atomic
      transaction (supporting SUCCESS, PARTIAL, FAILED), protects failed weeks from
      soft-deletion, enforces per-user distributed locking with heartbeat renewal,
      and fails fast on expired/401 JWT without speculative refresh.
    """

    def __init__(
        self,
        db: Session,
        provider: PortalProvider,
        session_manager: PortalSessionManager,
        lock_provider: RedisDistributedLock | None = None,
        week_delay_seconds: float | None = None,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        settings = get_settings()
        self.db = db
        self.provider = provider
        self.session_manager = session_manager
        self.subject_service = SubjectService(db)
        self.schedule_service = ScheduleService(db)
        self.lock_provider = lock_provider or RedisDistributedLock()
        self.week_delay_seconds = (
            week_delay_seconds
            if week_delay_seconds is not None
            else settings.portal_week_request_delay_seconds
        )
        self._sleep = sleep_fn

    def _mark_sync_log_failed(
        self,
        sync_log_id: int,
        error_code: str,
        error_message: str,
        *,
        status: SyncStatus = SyncStatus.FAILED,
        items_added: int = 0,
        items_updated: int = 0,
        items_removed: int = 0,
    ) -> bool:
        """
        Persist SyncLog=FAILED/PARTIAL in its own committed transaction (immune to prior rollback).
        Failure-safe: if updating SyncLog fails, rolls back and logs the secondary error
        without exposing secrets and without replacing the primary failure.
        """
        try:
            log = self.db.get(SyncLog, sync_log_id)
            if log is None:
                return False
            log.status = status
            log.items_added = items_added
            log.items_updated = items_updated
            log.items_removed = items_removed
            log.error_code = error_code
            log.error_message = redact_sensitive(error_message)[:1000]
            log.finished_at = utc_now()
            self.db.commit()
            return True
        except Exception as secondary_exc:
            try:
                self.db.rollback()
            except Exception:
                pass
            logger.error(
                "Secondary failure while marking Portal SyncLog id=%s as %s: %s",
                sync_log_id,
                status.value,
                type(secondary_exc).__name__,
            )
            return False

    def _fetch_single_week(
        self,
        bundle: PortalTokenBundle,
        week_monday: date,
    ) -> list[PortalScheduleItem]:
        """Use single-week API when available (UTHPortalHttpClient), else 7-day range."""
        fetch_week = getattr(self.provider, "fetch_schedule_for_week", None)
        if callable(fetch_week):
            return fetch_week(
                access_token=bundle.access_token,
                anchor_date=week_monday,
                csrf_token=bundle.csrf_token,
            )
        return self.provider.fetch_weekly_schedule(
            access_token=bundle.access_token,
            start_date=week_monday,
            end_date=week_monday + timedelta(days=6),
            csrf_token=bundle.csrf_token,
        )

    def _persist_items_in_current_tx(
        self,
        user_id: int,
        raw_items: list[PortalScheduleItem],
        range_start: date,
        range_end: date,
    ) -> tuple[int, int, int]:
        """
        Upsert subjects & schedules with commit=False and soft-delete schedules missing
        strictly within the actual returned date range intersected with [range_start, range_end].
        Caller owns commit/rollback.
        """
        deduped_items = list({item.source_event_id: item for item in raw_items}.values())

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
                commit=False,
            )
            already_exists = (
                item.source_event_id in existing_event_ids
                or self.schedule_service.repo.get_by_source_event_id(
                    user_id, item.source_event_id
                )
                is not None
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
                commit=False,
            )
            if already_exists:
                updated += 1
            else:
                added += 1

        removed = 0
        for old_sched in existing_in_window:
            if old_sched.source_event_id not in incoming_event_ids:
                old_sched.is_deleted = True
                removed += 1

        return added, updated, removed

    def sync_schedules(
        self,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        sync_type: SyncType = SyncType.MANUAL,
    ) -> PortalSyncResult:
        with self.lock_provider.acquire(f"portal:sync:user:{user_id}", ttl_seconds=120) as lease:
            if not lease:
                return PortalSyncResult(
                    sync_log_id=0,
                    status=SyncStatus.FAILED,
                    is_mock=self.provider.is_mock,
                    items_added=0,
                    items_updated=0,
                    items_removed=0,
                    error_code="PORTAL_SYNC_IN_PROGRESS",
                    error_message="Another Portal sync is already running for this user.",
                )
            return self._sync_schedules_locked(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                sync_type=sync_type,
            )

    def _sync_schedules_locked(
        self,
        user_id: int,
        start_date: date | None,
        end_date: date | None,
        sync_type: SyncType,
    ) -> PortalSyncResult:
        ref = local_today()
        range_start = start_date or (ref - timedelta(days=ref.weekday()))
        range_end = end_date or (range_start + timedelta(days=6))

        # Transaction 1: record RUNNING sync log immediately
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
        sync_log_id = sync_log.id

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
            logger.warning(
                "Portal sync failed for user_id=%s error_code=%s",
                user_id,
                exc.error_code,
            )
            self._mark_sync_log_failed(sync_log_id, exc.error_code, str(exc))
            return PortalSyncResult(
                sync_log_id=sync_log_id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=exc.error_code,
                error_message=str(exc),
                reauth_required=isinstance(exc, PortalReauthRequiredError),
            )
        except Exception as exc:
            self.db.rollback()
            err_code = "PORTAL_FETCH_UNEXPECTED_ERROR"
            err_msg = f"Unexpected error while fetching Portal schedule ({type(exc).__name__})."
            logger.warning(
                "Portal fetch unexpected failure for user_id=%s error_code=%s exc=%s",
                user_id,
                err_code,
                type(exc).__name__,
            )
            self._mark_sync_log_failed(sync_log_id, err_code, err_msg)
            return PortalSyncResult(
                sync_log_id=sync_log_id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=err_code,
                error_message=err_msg,
            )

        # Transaction 2: atomic subject upserts + schedule upserts + soft-deletes + SyncLog=SUCCESS
        try:
            added, updated, removed = self._persist_items_in_current_tx(
                user_id=user_id,
                raw_items=raw_items,
                range_start=range_start,
                range_end=range_end,
            )
            log_row = self.db.get(SyncLog, sync_log_id)
            if log_row is not None:
                log_row.status = SyncStatus.SUCCESS
                log_row.items_added = added
                log_row.items_updated = updated
                log_row.items_removed = removed
                log_row.finished_at = utc_now()

            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            err_code = getattr(exc, "error_code", "PORTAL_SYNC_TRANSACTION_ERROR")
            err_msg = f"Portal schedule sync failed and rolled back ({type(exc).__name__})."
            logger.warning(
                "Portal sync transaction rolled back for user_id=%s error_code=%s exc=%s",
                user_id,
                err_code,
                type(exc).__name__,
            )
            self._mark_sync_log_failed(sync_log_id, err_code, err_msg)
            return PortalSyncResult(
                sync_log_id=sync_log_id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=err_code,
                error_message=err_msg,
            )

        self.session_manager.mark_sync_success(user_id)

        return PortalSyncResult(
            sync_log_id=sync_log_id,
            status=SyncStatus.SUCCESS,
            is_mock=self.provider.is_mock,
            items_added=added,
            items_updated=updated,
            items_removed=removed,
            weeks_total=1,
            weeks_succeeded=1,
            weeks_failed=0,
        )

    def sync_semester_schedules(
        self,
        user_id: int,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        sync_type: SyncType = SyncType.FULL,
        today: date | None = None,
        configured_start: str | None = None,
        configured_end: str | None = None,
        max_weeks: int | None = None,
    ) -> PortalSyncResult:
        """
        Synchronize all weeks from current week through the configured semester end date.
        Each week executes in an independent atomic transaction; failed weeks never delete
        existing schedules, and expired/401 JWT triggers immediate fail-fast.
        """
        try:
            sem_range = resolve_semester_range(
                start_date=start_date,
                end_date=end_date,
                today=today,
                configured_start=configured_start,
                configured_end=configured_end,
                max_weeks=max_weeks,
            )
        except PortalIntegrationError as exc:
            return PortalSyncResult(
                sync_log_id=0,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=exc.error_code,
                error_message=str(exc),
                weeks_total=0,
                weeks_succeeded=0,
                weeks_failed=0,
            )

        with self.lock_provider.acquire(
            f"portal:sync:user:{user_id}",
            ttl_seconds=180,
            auto_renew=True,
        ) as lease:
            if not lease:
                return PortalSyncResult(
                    sync_log_id=0,
                    status=SyncStatus.FAILED,
                    is_mock=self.provider.is_mock,
                    items_added=0,
                    items_updated=0,
                    items_removed=0,
                    error_code="PORTAL_SYNC_IN_PROGRESS",
                    error_message="Another Portal sync is already running for this user.",
                    weeks_total=len(sem_range.week_anchors),
                    weeks_succeeded=0,
                    weeks_failed=0,
                )
            return self._sync_semester_locked(
                user_id=user_id,
                lease=lease,
                sem_start=sem_range.start_date,
                sem_end=sem_range.end_date,
                week_anchors=sem_range.week_anchors,
                sync_type=sync_type,
            )

    def _sync_semester_locked(
        self,
        *,
        user_id: int,
        lease: LockLease,
        sem_start: date,
        sem_end: date,
        week_anchors: list[date],
        sync_type: SyncType,
    ) -> PortalSyncResult:
        weeks_total = len(week_anchors)

        # Transaction 0: record RUNNING sync log
        sync_log = SyncLog(
            user_id=user_id,
            source=SyncSource.PORTAL,
            sync_type=sync_type,
            status=SyncStatus.RUNNING,
            started_at=utc_now(),
        )
        self.db.add(sync_log)
        self.db.commit()
        sync_log_id = sync_log.id

        # Pre-flight JWT validation before looping across weeks
        try:
            self.session_manager.ensure_valid_bundle(user_id)
        except PortalReauthRequiredError as exc:
            logger.warning(
                "Semester sync aborted pre-flight (reauth required) for user_id=%s error_code=%s",
                user_id,
                exc.error_code,
            )
            self._mark_sync_log_failed(sync_log_id, exc.error_code, str(exc))
            return PortalSyncResult(
                sync_log_id=sync_log_id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                items_added=0,
                items_updated=0,
                items_removed=0,
                error_code=exc.error_code,
                error_message=str(exc),
                weeks_total=weeks_total,
                weeks_succeeded=0,
                weeks_failed=weeks_total,
                reauth_required=True,
            )

        total_added = 0
        total_updated = 0
        total_removed = 0
        weeks_succeeded = 0
        failed_weeks: list[tuple[date, str]] = []
        reauth_required = False
        lock_lost = False

        for idx, week_monday in enumerate(week_anchors):
            if lease.lost:
                lock_lost = True
                logger.warning(
                    "Semester sync stopped due to lost distributed lock for user_id=%s at week=%s",
                    user_id,
                    week_monday.isoformat(),
                )
                failed_weeks.append((week_monday, "PORTAL_SYNC_LOCK_LOST"))
                break

            if idx > 0 and self.week_delay_seconds > 0:
                self._sleep(self.week_delay_seconds)

            # 1. Fetch week from Portal
            try:
                week_items = self.session_manager.execute_authenticated(
                    user_id,
                    lambda bundle, anchor=week_monday: self._fetch_single_week(bundle, anchor),
                )
            except PortalReauthRequiredError as exc:
                reauth_required = True
                logger.warning(
                    "Semester sync fail-fast on reauth required for user_id=%s week=%s error_code=%s",
                    user_id,
                    week_monday.isoformat(),
                    exc.error_code,
                )
                failed_weeks.append((week_monday, exc.error_code))
                break
            except PortalIntegrationError as exc:
                logger.warning(
                    "Semester sync week fetch failed user_id=%s week=%s error_code=%s",
                    user_id,
                    week_monday.isoformat(),
                    exc.error_code,
                )
                failed_weeks.append((week_monday, exc.error_code))
                continue
            except Exception as exc:
                self.db.rollback()
                logger.warning(
                    "Semester sync unexpected fetch error user_id=%s week=%s exc=%s",
                    user_id,
                    week_monday.isoformat(),
                    type(exc).__name__,
                )
                failed_weeks.append((week_monday, "PORTAL_FETCH_UNEXPECTED_ERROR"))
                continue

            # 2. Persist single week in its own atomic transaction
            week_start = max(week_monday, sem_start)
            week_end = min(week_monday + timedelta(days=6), sem_end)
            try:
                added, updated, removed = self._persist_items_in_current_tx(
                    user_id=user_id,
                    raw_items=week_items,
                    range_start=week_start,
                    range_end=week_end,
                )
                self.db.commit()
                total_added += added
                total_updated += updated
                total_removed += removed
                weeks_succeeded += 1
            except Exception as exc:
                self.db.rollback()
                logger.warning(
                    "Semester sync week transaction rolled back user_id=%s week=%s exc=%s",
                    user_id,
                    week_monday.isoformat(),
                    type(exc).__name__,
                )
                failed_weeks.append((week_monday, "PORTAL_SYNC_TRANSACTION_ERROR"))

        weeks_failed = weeks_total - weeks_succeeded

        if weeks_failed == 0:
            try:
                log_row = self.db.get(SyncLog, sync_log_id)
                if log_row is not None:
                    log_row.status = SyncStatus.SUCCESS
                    log_row.items_added = total_added
                    log_row.items_updated = total_updated
                    log_row.items_removed = total_removed
                    log_row.finished_at = utc_now()
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                logger.error(
                    "Failed to finalize SUCCESS SyncLog id=%s: %s",
                    sync_log_id,
                    type(exc).__name__,
                )
            self.session_manager.mark_sync_success(user_id)
            return PortalSyncResult(
                sync_log_id=sync_log_id,
                status=SyncStatus.SUCCESS,
                is_mock=self.provider.is_mock,
                items_added=total_added,
                items_updated=total_updated,
                items_removed=total_removed,
                weeks_total=weeks_total,
                weeks_succeeded=weeks_succeeded,
                weeks_failed=0,
                reauth_required=False,
            )

        primary_code = (
            "REAUTH_REQUIRED"
            if reauth_required
            else "PORTAL_SYNC_LOCK_LOST"
            if lock_lost
            else failed_weeks[0][1]
        )
        summary = "; ".join(f"{wk.isoformat()}:{code}" for wk, code in failed_weeks[:8])
        final_status = SyncStatus.PARTIAL if weeks_succeeded > 0 else SyncStatus.FAILED
        err_msg = f"Completed {weeks_succeeded}/{weeks_total} weeks. Failed: {summary}"

        self._mark_sync_log_failed(
            sync_log_id,
            primary_code,
            err_msg,
            status=final_status,
            items_added=total_added,
            items_updated=total_updated,
            items_removed=total_removed,
        )
        if weeks_succeeded > 0 and not reauth_required:
            self.session_manager.mark_sync_success(user_id)

        return PortalSyncResult(
            sync_log_id=sync_log_id,
            status=final_status,
            is_mock=self.provider.is_mock,
            items_added=total_added,
            items_updated=total_updated,
            items_removed=total_removed,
            error_code=primary_code,
            error_message=err_msg,
            weeks_total=weeks_total,
            weeks_succeeded=weeks_succeeded,
            weeks_failed=weeks_failed,
            reauth_required=reauth_required,
        )
