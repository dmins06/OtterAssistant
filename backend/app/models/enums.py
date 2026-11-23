from enum import Enum


class SessionPeriod(str, Enum):
    """Buổi học trên Portal UTH (Sáng / Chiều / Tối)."""

    MORNING = "Sáng"
    AFTERNOON = "Chiều"
    EVENING = "Tối"


class SubmissionStatus(str, Enum):
    """Trạng thái bài tập LMS — không mặc định quá hạn là hết quyền nộp."""

    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTED = "SUBMITTED"
    GRADED = "GRADED"
    OVERDUE = "OVERDUE"
    NO_DEADLINE = "NO_DEADLINE"
    UNKNOWN = "UNKNOWN"


class ConnectionStatus(str, Enum):
    """Vòng đời kết nối Portal / LMS (Section 5.5)."""

    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    TOKEN_EXPIRING = "TOKEN_EXPIRING"
    REFRESHING = "REFRESHING"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ERROR = "ERROR"


class CourseMappingStatus(str, Enum):
    """Trạng thái liên kết học phần giữa Portal và LMS."""

    MAPPED = "MAPPED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"


class ReminderTargetType(str, Enum):
    SCHEDULE = "SCHEDULE"
    ASSIGNMENT = "ASSIGNMENT"
    CUSTOM = "CUSTOM"


class NotificationType(str, Enum):
    NEW_ASSIGNMENT = "NEW_ASSIGNMENT"
    DEADLINE_CHANGED = "DEADLINE_CHANGED"
    UPCOMING_DEADLINE = "UPCOMING_DEADLINE"
    OVERDUE_ASSIGNMENT = "OVERDUE_ASSIGNMENT"
    UPCOMING_CLASS = "UPCOMING_CLASS"
    DAILY_SUMMARY = "DAILY_SUMMARY"
    WEEKLY_SUMMARY = "WEEKLY_SUMMARY"
    PORTAL_SESSION_EXPIRED = "PORTAL_SESSION_EXPIRED"
    LMS_AUTH_REQUIRED = "LMS_AUTH_REQUIRED"
    SYNC_FAILED = "SYNC_FAILED"
    CUSTOM_REMINDER = "CUSTOM_REMINDER"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    POLICY_RESTRICTED = "POLICY_RESTRICTED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


class SyncSource(str, Enum):
    PORTAL = "PORTAL"
    LMS = "LMS"


class SyncType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    INCREMENTAL = "INCREMENTAL"
    FULL = "FULL"


class SyncStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
