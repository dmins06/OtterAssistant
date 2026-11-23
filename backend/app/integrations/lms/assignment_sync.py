from dataclasses import dataclass
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.integrations.lms.authentication import LMSAuthManager
from app.integrations.lms.client import LMSProvider
from app.integrations.lms.course_mapping import CourseMappingService
from app.integrations.lms.exceptions import LMSAuthRequiredError, LMSIntegrationError
from app.models.academic import Assignment, LMSCourse
from app.models.connection import SyncLog
from app.models.enums import SyncSource, SyncStatus, SyncType
from app.schemas.assignment import AssignmentUpsert
from app.services.assignment_service import AssignmentService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LMSSyncResult:
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


class LMSAssignmentSyncService:
    """
    Synchronizes LMS courses & assignments into MySQL.
    Preserves previously synced assignments when LMS is unreachable or blocked.
    """

    def __init__(
        self,
        db: Session,
        provider: LMSProvider,
        auth_manager: LMSAuthManager,
    ) -> None:
        self.db = db
        self.provider = provider
        self.auth_manager = auth_manager
        self.mapping_service = CourseMappingService(db)
        self.assignment_service = AssignmentService(db)

    def sync_assignments(
        self,
        user_id: int,
        sync_type: SyncType = SyncType.MANUAL,
    ) -> LMSSyncResult:
        started_at = utc_now()
        sync_log = SyncLog(
            user_id=user_id,
            source=SyncSource.LMS,
            sync_type=sync_type,
            status=SyncStatus.RUNNING,
            started_at=started_at,
        )
        self.db.add(sync_log)
        self.db.commit()
        self.db.refresh(sync_log)

        try:
            credentials = self.auth_manager.get_credentials(user_id)
            course_items, assignment_items = self.provider.fetch_courses_and_assignments(credentials)
        except LMSIntegrationError as exc:
            is_auth_err = isinstance(exc, LMSAuthRequiredError)
            self.auth_manager.mark_error(user_id, exc.error_code, require_reauth=is_auth_err)
            sync_log.status = SyncStatus.FAILED
            sync_log.error_code = exc.error_code
            sync_log.error_message = str(exc)
            sync_log.finished_at = utc_now()
            self.db.commit()
            logger.warning("LMS sync failed for user_id=%s error_code=%s", user_id, exc.error_code)
            return LMSSyncResult(
                sync_log_id=sync_log.id,
                status=SyncStatus.FAILED,
                is_mock=self.provider.is_mock,
                courses_synced=0,
                items_added=0,
                items_updated=0,
                items_removed=0,
                deadlines_changed=0,
                error_code=exc.error_code,
                error_message=str(exc),
            )

        now = utc_now()
        course_map_by_ext_id: dict[str, LMSCourse] = {}
        for c_item in course_items:
            course = self.db.scalar(
                select(LMSCourse).where(
                    LMSCourse.user_id == user_id,
                    LMSCourse.external_course_id == c_item.external_course_id,
                )
            )
            if course is None:
                course = LMSCourse(
                    user_id=user_id,
                    external_course_id=c_item.external_course_id,
                    course_code=c_item.course_code,
                    course_name=c_item.course_name,
                    course_url=c_item.course_url,
                    last_synced_at=now,
                )
                self.db.add(course)
                self.db.flush()
            else:
                course.course_name = c_item.course_name
                if c_item.course_code is not None:
                    course.course_code = c_item.course_code
                if c_item.course_url is not None:
                    course.course_url = c_item.course_url
                course.last_synced_at = now

            subject_id, mapping_status = self.mapping_service.resolve_mapping(user_id, course)
            course.subject_id = subject_id
            course.mapping_status = mapping_status
            self.db.flush()
            course_map_by_ext_id[c_item.external_course_id] = course

        self.db.commit()

        existing_assignments = list(
            self.db.scalars(
                select(Assignment).where(
                    Assignment.user_id == user_id,
                    Assignment.is_deleted.is_(False),
                )
            ).all()
        )
        incoming_keys: set[tuple[int, str]] = set()
        added = 0
        updated = 0
        deadlines_changed = 0

        for a_item in assignment_items:
            course = course_map_by_ext_id.get(a_item.external_course_id)
            if course is None:
                continue
            incoming_keys.add((course.id, a_item.external_assignment_id))
            upsert_res = self.assignment_service.upsert_assignment(
                user_id,
                AssignmentUpsert(
                    lms_course_id=course.id,
                    subject_id=course.subject_id,
                    external_assignment_id=a_item.external_assignment_id,
                    title=a_item.title,
                    description=a_item.description,
                    created_at_source=a_item.created_at_source,
                    due_at=a_item.due_at,
                    submission_status=a_item.submission_status,
                    submitted_at=a_item.submitted_at,
                    grade=a_item.grade,
                    feedback=a_item.feedback,
                    attachments_json=json.dumps(a_item.attachments) if a_item.attachments else None,
                    assignment_url=a_item.assignment_url,
                ),
            )
            if upsert_res.is_new:
                added += 1
            else:
                updated += 1
            if upsert_res.deadline_changed:
                deadlines_changed += 1

        # Soft-delete assignments no longer present in LMS
        removed = 0
        for old_a in existing_assignments:
            if (old_a.lms_course_id, old_a.external_assignment_id) not in incoming_keys:
                old_a.is_deleted = True
                removed += 1

        sync_log.status = SyncStatus.SUCCESS
        sync_log.items_added = added
        sync_log.items_updated = updated
        sync_log.items_removed = removed
        sync_log.finished_at = utc_now()
        self.db.commit()
        self.auth_manager.mark_sync_success(user_id)

        return LMSSyncResult(
            sync_log_id=sync_log.id,
            status=SyncStatus.SUCCESS,
            is_mock=self.provider.is_mock,
            courses_synced=len(course_map_by_ext_id),
            items_added=added,
            items_updated=updated,
            items_removed=removed,
            deadlines_changed=deadlines_changed,
        )
