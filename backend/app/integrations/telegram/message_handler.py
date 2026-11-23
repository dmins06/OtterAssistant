import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.assistant import OtterAssistantAgent
from app.agents.llm_provider import LLMProvider
from app.core.timezone import utc_now
from app.integrations.telegram.client import TelegramProvider
from app.integrations.telegram.linking import TelegramAccountLinkingService
from app.models.user import AccountLinking, User

logger = logging.getLogger(__name__)


class TelegramMessageHandler:
    """Processes incoming Telegram Bot webhook updates and routes queries to OtterAssistantAgent."""

    def __init__(
        self,
        db: Session,
        telegram_client: TelegramProvider,
        llm_provider: LLMProvider,
    ) -> None:
        self.db = db
        self.telegram_client = telegram_client
        self.llm_provider = llm_provider
        self.linking_service = TelegramAccountLinkingService(db)

    def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return {"handled": False, "reason": "no_message"}

        chat = message.get("chat") or {}
        chat_id_raw = chat.get("id")
        if chat_id_raw is None:
            return {"handled": False, "reason": "missing_chat_id"}
        chat_id = str(chat_id_raw)

        sender = message.get("from") or {}
        username = sender.get("username")
        first_name = sender.get("first_name") or ""
        display_name = f"@{username}" if username else (first_name.strip() or None)

        text = str(message.get("text") or "").strip()
        if not text:
            return {"handled": False, "reason": "empty_text"}

        # Handle /start or deep-link /start <CODE>
        if text.lower().startswith("/start"):
            parts = text.split(None, 1)
            if len(parts) == 2 and parts[1].strip():
                return self._process_link_command(chat_id, parts[1].strip(), display_name)
            self.telegram_client.send_message(
                chat_id,
                "Chào mừng bạn đến với Otter Assistant (UTH)!\n"
                "Để liên kết tài khoản sinh viên, hãy mở Otter Dashboard → Tích hợp → Telegram Bot, "
                "tạo mã liên kết và gửi: /link <MÃ_LIÊN_KẾT>",
            )
            return {"handled": True, "command": "start"}

        # Handle /link <CODE>
        if text.lower().startswith("/link"):
            parts = text.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                self.telegram_client.send_message(
                    chat_id,
                    "Cú pháp đúng: /link <MÃ_LIÊN_KẾT> (ví dụ: /link OTTER-1A2B3C).",
                )
                return {"handled": True, "linked": False}
            return self._process_link_command(chat_id, parts[1].strip(), display_name)

        # Handle /unlink
        if text.lower() == "/unlink":
            linking = self.db.scalar(
                select(AccountLinking).where(
                    AccountLinking.provider == "telegram",
                    AccountLinking.external_user_id == chat_id,
                    AccountLinking.is_active.is_(True),
                )
            )
            if linking:
                linking.is_active = False
                self.db.commit()
                self.telegram_client.send_message(chat_id, "Đã hủy liên kết Telegram với Otter Assistant.")
            else:
                self.telegram_client.send_message(chat_id, "Telegram của bạn hiện chưa liên kết tài khoản nào.")
            return {"handled": True, "command": "unlink"}

        # Route normal text to OtterAssistantAgent
        linking = self.db.scalar(
            select(AccountLinking).where(
                AccountLinking.provider == "telegram",
                AccountLinking.external_user_id == chat_id,
                AccountLinking.is_active.is_(True),
            )
        )
        if linking is None:
            self.telegram_client.send_message(
                chat_id,
                "Tài khoản Telegram của bạn chưa liên kết với Otter Assistant.\n"
                "Vui lòng mở Otter Dashboard → Tích hợp → Telegram Bot để lấy mã và gửi: /link <MÃ_LIÊN_KẾT>",
            )
            return {"handled": True, "linked": False}

        linking.last_interaction_at = utc_now()
        self.db.commit()

        user = self.db.scalar(select(User).where(User.id == linking.user_id, User.is_active.is_(True)))
        if user is None:
            return {"handled": False, "reason": "user_inactive"}

        agent = OtterAssistantAgent(db=self.db, llm_provider=self.llm_provider)
        reply = agent.chat(user=user, message=text, channel="telegram")
        delivery = self.telegram_client.send_message(chat_id, reply.reply_text)
        logger.info(
            "Telegram reply dispatched for user_id=%s status=%s",
            user.id,
            delivery.status.value,
        )
        return {
            "handled": True,
            "user_id": user.id,
            "delivery_status": delivery.status.value,
            "tools_used": reply.tools_used,
        }

    def _process_link_command(
        self,
        chat_id: str,
        code: str,
        display_name: str | None,
    ) -> dict[str, Any]:
        linked = self.linking_service.consume_link_code(code, chat_id, display_name=display_name)
        if linked is None:
            self.telegram_client.send_message(
                chat_id,
                "Mã liên kết không hợp lệ hoặc đã hết hạn. Vui lòng tạo mã mới trên Otter Dashboard.",
            )
            return {"handled": True, "linked": False}
        self.telegram_client.send_message(
            chat_id,
            "Liên kết tài khoản Otter Assistant thành công! "
            "Bạn có thể hỏi lịch học, deadline, tạo nhắc nhở và nhận thông báo tự động ngay trên Telegram.",
        )
        return {"handled": True, "linked": True, "user_id": linked.user_id}
