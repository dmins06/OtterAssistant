from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.academic import Subject


class SubjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_user(self, user_id: int) -> list[Subject]:
        stmt = (
            select(Subject)
            .where(Subject.user_id == user_id)
            .order_by(Subject.subject_code.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_id(self, user_id: int, subject_id: int) -> Subject | None:
        stmt = select(Subject).where(
            Subject.user_id == user_id,
            Subject.id == subject_id,
        )
        return self.db.scalar(stmt)

    def get_by_code(self, user_id: int, subject_code: str) -> Subject | None:
        stmt = select(Subject).where(
            Subject.user_id == user_id,
            Subject.subject_code == subject_code,
        )
        return self.db.scalar(stmt)

    def search_by_name_or_code(self, user_id: int, query: str) -> list[Subject]:
        like_pattern = f"%{query.strip()}%"
        stmt = (
            select(Subject)
            .where(
                Subject.user_id == user_id,
                (Subject.name.ilike(like_pattern)) | (Subject.subject_code.ilike(like_pattern)),
            )
            .order_by(Subject.name.asc())
        )
        return list(self.db.scalars(stmt).all())

    def add(self, subject: Subject) -> Subject:
        self.db.add(subject)
        self.db.flush()
        return subject
