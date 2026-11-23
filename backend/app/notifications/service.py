from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import ensure_utc, utc_now
from app.integrations.telegram.client import TelegramProvider
from app.integrations.telegram.linking import TelegramAccountLinkingService
from app.models.enums import NotificationStatus, NotificationType
from app.models.notification import NotificationLog

logger = logging.getLogger(__name__)


def build_idempotency_key(
    user_id: int,
    event_id: str,
    notification_type: NotificationType,
    scheduled_for: datetime,
) -> str:
    """
    Deterministic idempotency key per Section 9.4:
    Based on User ID + Event ID + Notification Type + Scheduled Time (minute precision UTC).
    """
    ts_utc = ensure_utc(scheduled_for) or utc_now()
    slot = ts_utc.strftime("%Y%m%dT%H%MZ")
    raw = f"{user_id}:{event_id}:{notification_type.value}:{slot}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"notif:{user_id}:{notification_type.value}:{digest}"


@dataclass(frozen=True)
class DispatchResult:
    sent: bool
    skipped_duplicate: bool
    status: NotificationStatus
    idempotency_key: str
    log_id: int | None = None


class NotificationService:
    """
    Central notification dispatcher enforcing:
      - Strict duplicate prevention via unique `idempotency_key` in `notification_logs`
      - Telegram Bot delivery with clean handling for unlinked/blocked users
      - Full delivery history auditing
    """

    def __init__(self, db: Session, telegram_client: TelegramProvider) -> None:
        self.db = db
        self.telegram_client = telegram_client
        self.linking_service = TelegramAccountLinkingService(db)

    def send_notification(
        self,
        user_id: int,
        event_id: str,
        notification_type: NotificationType,
        scheduled_for: datetime,
        message_text: str,
        channel: str = "telegram",
    ) -> DispatchResult:
        sched_utc = ensure_utc(scheduled_for) or utc_now()
        idem_key = build_idempotency_key(user_id, event_id, notification_type, sched_utc)

        existing = self.db.scalar(
            select(NotificationLog).where(NotificationLog.idempotency_key == idem_key)
        )
        if existing is not None:
            return DispatchResult(
                sent=False,
                skipped_duplicate=True,
                status=existing.status,
                idempotency_key=idem_key,
                log_id=existing.id,
            )

        linking = self.linking_service.get_user_linking(user_id)
        if linking is None:
            log = NotificationLog(
                user_id=user_id,
                idempotency_key=idem_key,
                notification_type=notification_type,
                channel=channel,
                status=NotificationStatus.POLICY_RESTRICTED,
                scheduled_for=sched_utc,
                payload_summary=message_text[:500],
                error_detail="No active Telegram account linking.",
            )
            self.db.add(log)
            self.db.commit()
            self.db.refresh(log)
            return DispatchResult(
                sent=False,
                skipped_duplicate=False,
                status=log.status,
                idempotency_key=idem_key,
                log_id=log.id,
            )

        delivery = self.telegram_client.send_message(
            chat_id=linking.external_user_id,
            text=message_text,
        )
        log = NotificationLog(
            user_id=user_id,
            idempotency_key=idem_key,
            notification_type=notification_type,
            channel=channel,
            status=delivery.status,
            scheduled_for=sched_utc,
            sent_at=utc_now() if delivery.ok else None,
            payload_summary=message_text[:500],
            error_detail=delivery.error_detail,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        logger.info(
            "Notification processed user_id=%s type=%s status=%s",
            user_id,
            notification_type.value,
            delivery.status.value,
        )
        return DispatchResult(
            sent=delivery.ok,
            skipped_duplicate=False,
            status=delivery.status,
            idempotency_key=idem_key,
            log_id=log.id,
        )
