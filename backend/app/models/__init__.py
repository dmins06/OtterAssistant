"""SQLAlchemy 2.x ORM models for Otter Assistant (14 core tables)."""

from app.models.academic import Assignment, LMSCourse, Schedule, Subject
from app.models.agent import AgentConversation, AgentMemory
from app.models.connection import LMSConnection, PortalConnection, SyncLog
from app.models.notification import NotificationLog, Reminder
from app.models.user import AccountLinking, User, UserPreference

__all__ = [
    "AccountLinking",
    "AgentConversation",
    "AgentMemory",
    "Assignment",
    "LMSConnection",
    "LMSCourse",
    "NotificationLog",
    "PortalConnection",
    "Reminder",
    "Schedule",
    "Subject",
    "SyncLog",
    "User",
    "UserPreference",
]
