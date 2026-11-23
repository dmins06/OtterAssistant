from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Reminder


class ReminderRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_user(self, user_id: int, only_enabled: bool = False) -> list[Reminder]:
        stmt = select(Reminder).where(Reminder.user_id == user_id)
        if only_enabled:
            stmt = stmt.where(Reminder.is_enabled.is_(True))
        stmt = stmt.order_by(Reminder.remind_at.asc(), Reminder.id.asc())
        return list(self.db.scalars(stmt).all())

    def get_by_id(self, user_id: int, reminder_id: int) -> Reminder | None:
        stmt = select(Reminder).where(
            Reminder.user_id == user_id,
            Reminder.id == reminder_id,
        )
        return self.db.scalar(stmt)

    def add(self, reminder: Reminder) -> Reminder:
        self.db.add(reminder)
        self.db.flush()
        return reminder

    def delete(self, reminder: Reminder) -> None:
        self.db.delete(reminder)
        self.db.flush()
