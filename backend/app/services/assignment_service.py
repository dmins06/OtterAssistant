from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.timezone import ensure_utc, local_today, to_local_day_bounds_utc, utc_now
from app.models.academic import Assignment
from app.models.enums import SubmissionStatus
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.subject_repository import SubjectRepository
from app.schemas.assignment import AssignmentUpsert


@dataclass(frozen=True)
class AssignmentUpsertResult:
    assignment: Assignment
    is_new: bool
    deadline_changed: bool
    previous_due_at: datetime | None


class AssignmentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AssignmentRepository(db)
        self.subject_repo = SubjectRepository(db)

    def list_assignments(
        self,
        user_id: int,
        subject_id: int | None = None,
        status: SubmissionStatus | None = None,
    ) -> list[Assignment]:
        return self.repo.list_by_user(user_id, subject_id=subject_id, status=status)

    def get_assignment_detail(self, user_id: int, assignment_id: int) -> Assignment | None:
        return self.repo.get_by_id(user_id, assignment_id)

    def get_deadlines_for_date(self, user_id: int, target_date: date) -> list[Assignment]:
        start_utc, end_utc = to_local_day_bounds_utc(target_date)
        return self.repo.list_by_user(user_id, due_from=start_utc, due_to=end_utc)

    def get_today_deadlines(self, user_id: int, today: date | None = None) -> list[Assignment]:
        return self.get_deadlines_for_date(user_id, today or local_today())

    def get_tomorrow_deadlines(self, user_id: int, today: date | None = None) -> list[Assignment]:
        return self.get_deadlines_for_date(user_id, (today or local_today()) + timedelta(days=1))

    def get_upcoming_deadlines(
        self,
        user_id: int,
        days: int = 7,
        now_utc: datetime | None = None,
    ) -> list[Assignment]:
        start = ensure_utc(now_utc) or utc_now()
        end = start + timedelta(days=days)
        return self.repo.list_by_user(
            user_id,
            statuses=[SubmissionStatus.NOT_SUBMITTED, SubmissionStatus.UNKNOWN],
            due_from=start,
            due_to=end,
        )

    def get_overdue_assignments(
        self,
        user_id: int,
        now_utc: datetime | None = None,
    ) -> list[Assignment]:
        now = ensure_utc(now_utc) or utc_now()
        by_status = self.repo.list_by_user(user_id, status=SubmissionStatus.OVERDUE)
        past_due_unsubmitted = self.repo.list_by_user(
            user_id,
            statuses=[SubmissionStatus.NOT_SUBMITTED, SubmissionStatus.UNKNOWN],
            due_to=now,
        )
        seen: dict[int, Assignment] = {a.id: a for a in by_status}
        for item in past_due_unsubmitted:
            seen.setdefault(item.id, item)
        return sorted(seen.values(), key=lambda a: (a.due_at is None, a.due_at or now, a.id))

    def get_pending_assignments(self, user_id: int) -> list[Assignment]:
        return self.repo.list_by_user(
            user_id,
            statuses=[
                SubmissionStatus.NOT_SUBMITTED,
                SubmissionStatus.OVERDUE,
                SubmissionStatus.NO_DEADLINE,
                SubmissionStatus.UNKNOWN,
            ],
        )

    def get_submitted_assignments(self, user_id: int) -> list[Assignment]:
        return self.repo.list_by_user(
            user_id,
            statuses=[SubmissionStatus.SUBMITTED, SubmissionStatus.GRADED],
        )

    def upsert_assignment(self, user_id: int, payload: AssignmentUpsert) -> AssignmentUpsertResult:
        """
        Idempotent upsert for LMS assignments.
        Rule (Section 6.3): Never overwrite existing non-null fields with None when source omits them.
        """
        course = self.repo.get_lms_course_for_user(user_id, payload.lms_course_id)
        if course is None:
            raise PermissionError("LMS course not found or does not belong to current user.")

        if payload.subject_id is not None:
            subject = self.subject_repo.get_by_id(user_id, payload.subject_id)
            if subject is None:
                raise PermissionError("Subject not found or does not belong to current user.")

        now = utc_now()
        due_at_utc = ensure_utc(payload.due_at)
        submitted_at_utc = ensure_utc(payload.submitted_at)
        created_at_source_utc = ensure_utc(payload.created_at_source)

        existing = self.repo.get_by_external_id(
            user_id,
            payload.lms_course_id,
            payload.external_assignment_id,
        )
        if existing:
            prev_due = ensure_utc(existing.due_at)
            deadline_changed = due_at_utc is not None and prev_due != due_at_utc

            existing.title = payload.title
            if payload.subject_id is not None:
                existing.subject_id = payload.subject_id
            if payload.description is not None:
                existing.description = payload.description
            if created_at_source_utc is not None:
                existing.created_at_source = created_at_source_utc
            if due_at_utc is not None:
                existing.due_at = due_at_utc
            if payload.submission_status != SubmissionStatus.UNKNOWN or existing.submission_status == SubmissionStatus.UNKNOWN:
                existing.submission_status = payload.submission_status
            if submitted_at_utc is not None:
                existing.submitted_at = submitted_at_utc
            if payload.grade is not None:
                existing.grade = payload.grade
            if payload.feedback is not None:
                existing.feedback = payload.feedback
            if payload.attachments_json is not None:
                existing.attachments_json = payload.attachments_json
            if payload.assignment_url is not None:
                existing.assignment_url = payload.assignment_url
            existing.is_deleted = False
            existing.last_synced_at = now

            self.db.commit()
            self.db.refresh(existing)
            return AssignmentUpsertResult(
                assignment=existing,
                is_new=False,
                deadline_changed=deadline_changed,
                previous_due_at=prev_due,
            )

        assignment = Assignment(
            user_id=user_id,
            subject_id=payload.subject_id if payload.subject_id is not None else course.subject_id,
            lms_course_id=payload.lms_course_id,
            external_assignment_id=payload.external_assignment_id,
            title=payload.title,
            description=payload.description,
            created_at_source=created_at_source_utc,
            due_at=due_at_utc,
            submission_status=payload.submission_status,
            submitted_at=submitted_at_utc,
            grade=payload.grade,
            feedback=payload.feedback,
            attachments_json=payload.attachments_json,
            assignment_url=payload.assignment_url,
            is_deleted=False,
            last_synced_at=now,
        )
        self.repo.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return AssignmentUpsertResult(
            assignment=assignment,
            is_new=True,
            deadline_changed=False,
            previous_due_at=None,
        )
