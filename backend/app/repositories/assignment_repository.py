from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.academic import Assignment, LMSCourse
from app.models.enums import SubmissionStatus


class AssignmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: int, assignment_id: int) -> Assignment | None:
        stmt = select(Assignment).where(
            Assignment.user_id == user_id,
            Assignment.id == assignment_id,
            Assignment.is_deleted.is_(False),
        )
        return self.db.scalar(stmt)

    def get_by_external_id(
        self,
        user_id: int,
        lms_course_id: int,
        external_assignment_id: str,
    ) -> Assignment | None:
        stmt = select(Assignment).where(
            Assignment.user_id == user_id,
            Assignment.lms_course_id == lms_course_id,
            Assignment.external_assignment_id == external_assignment_id,
        )
        return self.db.scalar(stmt)

    def get_lms_course_for_user(self, user_id: int, lms_course_id: int) -> LMSCourse | None:
        stmt = select(LMSCourse).where(
            LMSCourse.user_id == user_id,
            LMSCourse.id == lms_course_id,
        )
        return self.db.scalar(stmt)

    def list_by_user(
        self,
        user_id: int,
        subject_id: int | None = None,
        status: SubmissionStatus | None = None,
        statuses: list[SubmissionStatus] | None = None,
        due_from: datetime | None = None,
        due_to: datetime | None = None,
    ) -> list[Assignment]:
        stmt = select(Assignment).where(
            Assignment.user_id == user_id,
            Assignment.is_deleted.is_(False),
        )
        if subject_id is not None:
            stmt = stmt.where(Assignment.subject_id == subject_id)
        if status is not None:
            stmt = stmt.where(Assignment.submission_status == status)
        if statuses:
            stmt = stmt.where(Assignment.submission_status.in_(statuses))
        if due_from is not None:
            stmt = stmt.where(Assignment.due_at >= due_from)
        if due_to is not None:
            stmt = stmt.where(Assignment.due_at <= due_to)

        stmt = stmt.order_by(Assignment.due_at.is_(None), Assignment.due_at.asc(), Assignment.id.asc())
        return list(self.db.scalars(stmt).all())

    def add(self, assignment: Assignment) -> Assignment:
        self.db.add(assignment)
        self.db.flush()
        return assignment
