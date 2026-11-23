from dataclasses import dataclass
import logging
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.models.enums import NotificationStatus

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramDeliveryResult:
    ok: bool
    status: NotificationStatus
    message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


class TelegramProvider(Protocol):
    def send_message(self, chat_id: str, text: str) -> TelegramDeliveryResult:
        ...


class TelegramBotClient:
    """Official Telegram Bot API client (`sendMessage`)."""

    def __init__(
        self,
        bot_token: str | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def send_message(self, chat_id: str, text: str) -> TelegramDeliveryResult:
        if not self.bot_token:
            logger.warning("Telegram sendMessage skipped: TELEGRAM_BOT_TOKEN is not configured.")
            return TelegramDeliveryResult(
                ok=False,
                status=NotificationStatus.FAILED,
                error_code="TELEGRAM_BOT_TOKEN_MISSING",
                error_detail="TELEGRAM_BOT_TOKEN is not configured.",
            )

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, json=payload)
                data: dict[str, Any] = response.json()
        except Exception as exc:
            logger.warning("Telegram sendMessage network error: %s", type(exc).__name__)
            return TelegramDeliveryResult(
                ok=False,
                status=NotificationStatus.FAILED,
                error_code="TELEGRAM_NETWORK_ERROR",
                error_detail=type(exc).__name__,
            )

        if data.get("ok") is True:
            msg_id = (data.get("result") or {}).get("message_id")
            message_id = str(msg_id) if msg_id is not None else None
            logger.info("Telegram sendMessage succeeded: message_id=%s", message_id)
            return TelegramDeliveryResult(
                ok=True,
                status=NotificationStatus.SENT,
                message_id=message_id,
            )

        err_code = int(data.get("error_code", response.status_code))
        description = str(data.get("description", "Telegram API error"))
        logger.warning(
            "Telegram sendMessage failed: http_status=%s error_code=%s description=%s",
            response.status_code,
            err_code,
            description,
        )
        if err_code == 403:
            return TelegramDeliveryResult(
                ok=False,
                status=NotificationStatus.POLICY_RESTRICTED,
                error_code="TELEGRAM_403_BLOCKED",
                error_detail=description,
            )
        if err_code == 429:
            return TelegramDeliveryResult(
                ok=False,
                status=NotificationStatus.QUOTA_EXCEEDED,
                error_code="TELEGRAM_429_RATE_LIMITED",
                error_detail=description,
            )
        return TelegramDeliveryResult(
            ok=False,
            status=NotificationStatus.FAILED,
            error_code=f"TELEGRAM_{err_code}",
            error_detail=description,
        )


class MockTelegramBotClient:
    """In-memory mock Telegram Bot client for unit & integration tests."""

    def __init__(self, blocked_chat_ids: set[str] | None = None) -> None:
        self.sent_messages: list[dict[str, str]] = []
        self.blocked_chat_ids = blocked_chat_ids or set()

    def send_message(self, chat_id: str, text: str) -> TelegramDeliveryResult:
        if chat_id in self.blocked_chat_ids:
            return TelegramDeliveryResult(
                ok=False,
                status=NotificationStatus.POLICY_RESTRICTED,
                error_code="TELEGRAM_403_BLOCKED",
                error_detail="Forbidden: bot was blocked by the user",
            )
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return TelegramDeliveryResult(
            ok=True,
            status=NotificationStatus.SENT,
            message_id=f"tg-mock-{len(self.sent_messages)}",
        )
