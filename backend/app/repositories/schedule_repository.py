from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.academic import Schedule, Subject


class ScheduleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: int, schedule_id: int) -> Schedule | None:
        stmt = (
            select(Schedule)
            .options(joinedload(Schedule.subject))
            .where(
                Schedule.user_id == user_id,
                Schedule.id == schedule_id,
                Schedule.is_deleted.is_(False),
            )
        )
        return self.db.scalar(stmt)

    def get_by_source_event_id(self, user_id: int, source_event_id: str) -> Schedule | None:
        stmt = select(Schedule).where(
            Schedule.user_id == user_id,
            Schedule.source_event_id == source_event_id,
        )
        return self.db.scalar(stmt)

    def list_by_date_range(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
    ) -> list[Schedule]:
        stmt = (
            select(Schedule)
            .options(joinedload(Schedule.subject))
            .where(
                Schedule.user_id == user_id,
                Schedule.is_deleted.is_(False),
                Schedule.class_date >= start_date,
                Schedule.class_date <= end_date,
            )
            .order_by(Schedule.class_date.asc(), Schedule.start_time.asc(), Schedule.period_start.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_subject(self, user_id: int, subject_id: int) -> list[Schedule]:
        stmt = (
            select(Schedule)
            .options(joinedload(Schedule.subject))
            .where(
                Schedule.user_id == user_id,
                Schedule.subject_id == subject_id,
                Schedule.is_deleted.is_(False),
            )
            .order_by(Schedule.class_date.asc(), Schedule.start_time.asc())
        )
        return list(self.db.scalars(stmt).all())

    def search_rooms(self, user_id: int, query: str) -> list[Schedule]:
        like_pattern = f"%{query.strip()}%"
        stmt = (
            select(Schedule)
            .join(Subject, Schedule.subject_id == Subject.id)
            .options(joinedload(Schedule.subject))
            .where(
                Schedule.user_id == user_id,
                Schedule.is_deleted.is_(False),
                (Subject.name.ilike(like_pattern))
                | (Subject.subject_code.ilike(like_pattern))
                | (Schedule.room.ilike(like_pattern))
                | (Schedule.campus.ilike(like_pattern)),
            )
            .order_by(Schedule.class_date.asc(), Schedule.start_time.asc())
        )
        return list(self.db.scalars(stmt).all())

    def add(self, schedule: Schedule) -> Schedule:
        self.db.add(schedule)
        self.db.flush()
        return schedule
