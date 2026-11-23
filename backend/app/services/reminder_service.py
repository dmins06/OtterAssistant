from sqlalchemy.orm import Session

from app.core.timezone import ensure_utc
from app.models.notification import Reminder
from app.repositories.reminder_repository import ReminderRepository
from app.schemas.reminder import ReminderCreate, ReminderUpdate


class ReminderService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ReminderRepository(db)

    def list_reminders(self, user_id: int, only_enabled: bool = False) -> list[Reminder]:
        return self.repo.list_by_user(user_id, only_enabled=only_enabled)

    def get_reminder(self, user_id: int, reminder_id: int) -> Reminder | None:
        return self.repo.get_by_id(user_id, reminder_id)

    def create_reminder(self, user_id: int, payload: ReminderCreate) -> Reminder:
        remind_at_utc = ensure_utc(payload.remind_at)
        assert remind_at_utc is not None
        reminder = Reminder(
            user_id=user_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            title=payload.title,
            notes=payload.notes,
            remind_at=remind_at_utc,
            is_enabled=payload.is_enabled,
            is_sent=False,
        )
        self.repo.add(reminder)
        self.db.commit()
        self.db.refresh(reminder)
        return reminder

    def update_reminder(
        self,
        user_id: int,
        reminder_id: int,
        payload: ReminderUpdate,
    ) -> Reminder | None:
        reminder = self.repo.get_by_id(user_id, reminder_id)
        if reminder is None:
            return None

        if payload.title is not None:
            reminder.title = payload.title
        if payload.notes is not None:
            reminder.notes = payload.notes
        if payload.remind_at is not None:
            remind_at_utc = ensure_utc(payload.remind_at)
            assert remind_at_utc is not None
            reminder.remind_at = remind_at_utc
            reminder.is_sent = False
        if payload.is_enabled is not None:
            reminder.is_enabled = payload.is_enabled

        self.db.commit()
        self.db.refresh(reminder)
        return reminder

    def delete_reminder(self, user_id: int, reminder_id: int) -> bool:
        reminder = self.repo.get_by_id(user_id, reminder_id)
        if reminder is None:
            return False
        self.repo.delete(reminder)
        self.db.commit()
        return True
