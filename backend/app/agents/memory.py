import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import redact_sensitive
from app.models.agent import AgentConversation, AgentMemory


class ConversationMemoryManager:
    """
    Manages user-isolated conversation history and non-sensitive agent memories in MySQL.
    Never stores raw credentials or JWTs.
    """

    def __init__(self, db: Session, max_history_messages: int = 12) -> None:
        self.db = db
        self.max_history_messages = max_history_messages

    def add_message(
        self,
        user_id: int,
        role: str,
        content: str,
        channel: str = "dashboard",
        tool_calls: list[dict[str, Any]] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> AgentConversation:
        entry = AgentConversation(
            user_id=user_id,
            channel=channel,
            role=role,
            content=redact_sensitive(content),
            tool_calls_json=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_recent_messages(
        self,
        user_id: int,
        limit: int | None = None,
    ) -> list[AgentConversation]:
        cap = limit or self.max_history_messages
        stmt = (
            select(AgentConversation)
            .where(AgentConversation.user_id == user_id)
            .order_by(AgentConversation.id.desc())
            .limit(cap)
        )
        rows = list(self.db.scalars(stmt).all())
        rows.reverse()
        return rows

    def set_memory(self, user_id: int, key: str, value: str) -> AgentMemory:
        sanitized_val = redact_sensitive(value)
        existing = self.db.scalar(
            select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_key == key,
            )
        )
        if existing:
            existing.memory_value = sanitized_val
            self.db.commit()
            self.db.refresh(existing)
            return existing

        mem = AgentMemory(user_id=user_id, memory_key=key, memory_value=sanitized_val)
        self.db.add(mem)
        self.db.commit()
        self.db.refresh(mem)
        return mem

    def get_memories(self, user_id: int) -> dict[str, str]:
        rows = list(
            self.db.scalars(
                select(AgentMemory).where(AgentMemory.user_id == user_id)
            ).all()
        )
        return {r.memory_key: r.memory_value for r in rows}
