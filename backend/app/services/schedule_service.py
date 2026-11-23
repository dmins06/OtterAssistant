from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.timezone import local_now, local_today, utc_now
from app.models.academic import Schedule
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.subject_repository import SubjectRepository
from app.schemas.schedule import ScheduleConflict, ScheduleUpsert


class ScheduleService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ScheduleRepository(db)
        self.subject_repo = SubjectRepository(db)

    def get_schedule_by_date(self, user_id: int, target_date: date) -> list[Schedule]:
        return self.repo.list_by_date_range(user_id, target_date, target_date)

    def get_today_schedule(self, user_id: int, today: date | None = None) -> list[Schedule]:
        return self.get_schedule_by_date(user_id, today or local_today())

    def get_tomorrow_schedule(self, user_id: int, today: date | None = None) -> list[Schedule]:
        base = today or local_today()
        return self.get_schedule_by_date(user_id, base + timedelta(days=1))

    def get_weekly_schedule(self, user_id: int, reference_date: date | None = None) -> list[Schedule]:
        ref = reference_date or local_today()
        monday = ref - timedelta(days=ref.weekday())
        sunday = monday + timedelta(days=6)
        return self.repo.list_by_date_range(user_id, monday, sunday)

    def get_subject_schedule(self, user_id: int, subject_id: int) -> list[Schedule]:
        return self.repo.list_by_subject(user_id, subject_id)

    def find_rooms(self, user_id: int, query: str) -> list[Schedule]:
        return self.repo.search_rooms(user_id, query)

    def is_empty_day(self, user_id: int, target_date: date) -> bool:
        return len(self.get_schedule_by_date(user_id, target_date)) == 0

    def get_next_class(self, user_id: int, now_local: datetime | None = None) -> Schedule | None:
        now = now_local or local_now()
        today = now.date()
        current_time = now.time().replace(tzinfo=None)
        upcoming = self.repo.list_by_date_range(user_id, today, today + timedelta(days=14))
        for item in upcoming:
            if item.class_date > today or (item.class_date == today and item.end_time > current_time):
                return item
        return None

    def detect_conflicts(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
    ) -> list[ScheduleConflict]:
        items = self.repo.list_by_date_range(user_id, start_date, end_date)
        by_day: dict[date, list[Schedule]] = defaultdict(list)
        for item in items:
            by_day[item.class_date].append(item)

        conflicts: list[ScheduleConflict] = []
        for day, day_items in by_day.items():
            ordered = sorted(day_items, key=lambda s: (s.start_time, s.end_time, s.id))
            for i in range(len(ordered)):
                for j in range(i + 1, len(ordered)):
                    first, second = ordered[i], ordered[j]
                    if second.start_time < first.end_time:
                        conflicts.append(
                            ScheduleConflict(
                                class_date=day,
                                first_schedule_id=first.id,
                                second_schedule_id=second.id,
                                first_subject_name=first.subject.name if first.subject else None,
                                second_subject_name=second.subject.name if second.subject else None,
                                overlap_start=max(first.start_time, second.start_time),
                                overlap_end=min(first.end_time, second.end_time),
                            )
                        )
        return conflicts

    def upsert_schedule(
        self,
        user_id: int,
        payload: ScheduleUpsert,
        commit: bool = True,
    ) -> Schedule:
        subject = self.subject_repo.get_by_id(user_id, payload.subject_id)
        if subject is None:
            raise PermissionError("Subject not found or does not belong to current user.")

        now = utc_now()
        existing = self.repo.get_by_source_event_id(user_id, payload.source_event_id)
        if existing:
            existing.subject_id = payload.subject_id
            existing.class_date = payload.class_date
            existing.weekday = payload.weekday
            existing.session = payload.session
            existing.class_session = payload.class_session
            existing.period_start = payload.period_start
            existing.period_end = payload.period_end
            existing.start_time = payload.start_time
            existing.end_time = payload.end_time
            if payload.room is not None:
                existing.room = payload.room
            if payload.campus is not None:
                existing.campus = payload.campus
            if payload.location is not None:
                existing.location = payload.location
            if payload.raw_room_text is not None:
                existing.raw_room_text = payload.raw_room_text
            if payload.syllabus_url is not None:
                existing.syllabus_url = payload.syllabus_url
            if payload.lms_url is not None:
                existing.lms_url = payload.lms_url
            existing.is_deleted = payload.is_deleted
            existing.last_synced_at = now
            if commit:
                self.db.commit()
                self.db.refresh(existing)
            else:
                self.db.flush()
            return existing

        schedule = Schedule(
            user_id=user_id,
            subject_id=payload.subject_id,
            source_event_id=payload.source_event_id,
            class_date=payload.class_date,
            weekday=payload.weekday,
            session=payload.session,
            class_session=payload.class_session,
            period_start=payload.period_start,
            period_end=payload.period_end,
            start_time=payload.start_time,
            end_time=payload.end_time,
            room=payload.room,
            campus=payload.campus,
            location=payload.location,
            raw_room_text=payload.raw_room_text,
            syllabus_url=payload.syllabus_url,
            lms_url=payload.lms_url,
            is_deleted=payload.is_deleted,
            last_synced_at=now,
        )
        self.repo.add(schedule)
        if commit:
            self.db.commit()
            self.db.refresh(schedule)
        else:
            self.db.flush()
        return schedule
