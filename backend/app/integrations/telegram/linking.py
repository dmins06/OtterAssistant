from datetime import datetime, timedelta, timezone
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.models.agent import AgentMemory
from app.models.user import AccountLinking

LINK_CODE_MEMORY_KEY = "telegram_link_code"
LINK_CODE_TTL_MINUTES = 10


class TelegramAccountLinkingService:
    """Secure one-time linking code flow binding Dashboard users to Telegram chats."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def generate_link_code(self, user_id: int) -> tuple[str, datetime]:
        code = f"OTTER-{secrets.token_hex(3).upper()}"
        expires_at = utc_now() + timedelta(minutes=LINK_CODE_TTL_MINUTES)
        payload = json.dumps({"code": code, "expires_at": expires_at.isoformat()})

        existing = self.db.scalar(
            select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_key == LINK_CODE_MEMORY_KEY,
            )
        )
        if existing:
            existing.memory_value = payload
        else:
            self.db.add(
                AgentMemory(
                    user_id=user_id,
                    memory_key=LINK_CODE_MEMORY_KEY,
                    memory_value=payload,
                )
            )
        self.db.commit()
        return code, expires_at

    def consume_link_code(
        self,
        code: str,
        chat_id: str,
        display_name: str | None = None,
    ) -> AccountLinking | None:
        normalized = code.strip().upper()
        candidates = list(
            self.db.scalars(
                select(AgentMemory).where(AgentMemory.memory_key == LINK_CODE_MEMORY_KEY)
            ).all()
        )
        now = utc_now()
        matched_mem: AgentMemory | None = None
        for mem in candidates:
            try:
                data = json.loads(mem.memory_value)
            except json.JSONDecodeError:
                continue
            if str(data.get("code", "")).upper() != normalized:
                continue
            exp = datetime.fromisoformat(data["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                self.db.delete(mem)
                self.db.commit()
                return None
            matched_mem = mem
            break

        if matched_mem is None:
            return None

        user_id = matched_mem.user_id
        self.db.delete(matched_mem)

        linking = self.db.scalar(
            select(AccountLinking).where(
                AccountLinking.provider == "telegram",
                AccountLinking.external_user_id == chat_id,
            )
        )
        if linking:
            linking.user_id = user_id
            linking.display_name = display_name or linking.display_name
            linking.is_active = True
            linking.last_interaction_at = now
        else:
            linking = AccountLinking(
                user_id=user_id,
                provider="telegram",
                external_user_id=chat_id,
                display_name=display_name,
                is_active=True,
                last_interaction_at=now,
            )
            self.db.add(linking)

        self.db.commit()
        self.db.refresh(linking)
        return linking

    def get_user_linking(self, user_id: int) -> AccountLinking | None:
        return self.db.scalar(
            select(AccountLinking).where(
                AccountLinking.user_id == user_id,
                AccountLinking.provider == "telegram",
                AccountLinking.is_active.is_(True),
            )
        )

    def unlink_user(self, user_id: int) -> bool:
        linking = self.get_user_linking(user_id)
        if linking is None:
            return False
        linking.is_active = False
        self.db.commit()
        return True
