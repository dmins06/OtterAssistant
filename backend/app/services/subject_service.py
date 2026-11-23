from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.models.academic import Subject
from app.repositories.subject_repository import SubjectRepository
from app.schemas.subject import SubjectUpsert


class SubjectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SubjectRepository(db)

    def list_subjects(self, user_id: int) -> list[Subject]:
        return self.repo.list_by_user(user_id)

    def get_subject(self, user_id: int, subject_id: int) -> Subject | None:
        return self.repo.get_by_id(user_id, subject_id)

    def search_subjects(self, user_id: int, query: str) -> list[Subject]:
        return self.repo.search_by_name_or_code(user_id, query)

    def upsert_subject(self, user_id: int, payload: SubjectUpsert) -> Subject:
        now = utc_now()
        existing = self.repo.get_by_code(user_id, payload.subject_code)
        if existing:
            existing.name = payload.name
            if payload.credits is not None:
                existing.credits = payload.credits
            if payload.lecturer is not None:
                existing.lecturer = payload.lecturer
            if payload.syllabus_url is not None:
                existing.syllabus_url = payload.syllabus_url
            if payload.lms_url is not None:
                existing.lms_url = payload.lms_url
            existing.last_synced_at = now
            self.db.commit()
            self.db.refresh(existing)
            return existing

        subject = Subject(
            user_id=user_id,
            subject_code=payload.subject_code,
            name=payload.name,
            credits=payload.credits,
            lecturer=payload.lecturer,
            syllabus_url=payload.syllabus_url,
            lms_url=payload.lms_url,
            last_synced_at=now,
        )
        self.repo.add(subject)
        self.db.commit()
        self.db.refresh(subject)
        return subject
