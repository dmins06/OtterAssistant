from dataclasses import dataclass, field
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
)
from app.agents.memory import ConversationMemoryManager
from app.agents.prompts.system_prompt import build_system_prompt
from app.core.timezone import local_now
from app.models.user import User
from app.tools.academic_tools import build_academic_tool_registry
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentReply:
    reply_text: str
    tools_used: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    degraded: bool = False


class OtterAssistantAgent:
    """
    Single Agent + Tool Calling orchestrator (Section 4.4 & Section 8).
    - Reads academic data exclusively through validated backend tools (MySQL Source of Truth)
    - Enforces strict user isolation by injecting `user.id` from authenticated context
    - Handles invalid tool calls, empty data, and Gemini Free Tier quota errors gracefully
    """

    def __init__(
        self,
        db: Session,
        llm_provider: LLMProvider,
        tool_registry: ToolRegistry | None = None,
        memory_manager: ConversationMemoryManager | None = None,
        max_tool_rounds: int = 5,
    ) -> None:
        self.db = db
        self.llm = llm_provider
        self.tools = tool_registry or build_academic_tool_registry(db)
        self.memory = memory_manager or ConversationMemoryManager(db)
        self.max_tool_rounds = max_tool_rounds

    def chat(
        self,
        user: User,
        message: str,
        channel: str = "dashboard",
    ) -> AgentReply:
        cleaned = message.strip()
        if not cleaned:
            return AgentReply(reply_text="Bạn muốn mình kiểm tra lịch học hay deadline môn nào?")

        # Persist user message first so no input is lost even if LLM hits quota
        self.memory.add_message(user.id, role="user", content=cleaned, channel=channel)

        history = self.memory.get_recent_messages(user.id)
        messages: list[dict[str, Any]] = [
            {"role": row.role, "content": row.content}
            for row in history
            if row.role in {"user", "assistant"}
        ]

        memories = self.memory.get_memories(user.id)
        system_prompt = build_system_prompt(local_now(), user.full_name)
        if memories:
            mem_lines = "\n".join(f"- {k}: {v}" for k, v in memories.items())
            system_prompt += f"\n\nNGỮ CẢNH CÁ NHÂN CỦA NGƯỜI DÙNG:\n{mem_lines}"

        declarations = self.tools.list_declarations()
        tools_used: list[str] = []
        tool_audit: list[dict[str, Any]] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        try:
            for _round in range(self.max_tool_rounds):
                llm_resp = self.llm.generate(
                    system_prompt=system_prompt,
                    messages=messages,
                    tool_declarations=declarations,
                )
                total_prompt_tokens += llm_resp.prompt_tokens
                total_completion_tokens += llm_resp.completion_tokens

                if not llm_resp.tool_calls:
                    final_text = (llm_resp.text or "").strip() or (
                        "Mình chưa có đủ thông tin để trả lời. Bạn có thể nói rõ hơn ngày hoặc môn học cần tra cứu không?"
                    )
                    self.memory.add_message(
                        user.id,
                        role="assistant",
                        content=final_text,
                        channel=channel,
                        tool_calls=tool_audit or None,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                    )
                    return AgentReply(
                        reply_text=final_text,
                        tools_used=tools_used,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                    )

                for call in llm_resp.tool_calls:
                    tools_used.append(call.name)
                    result = self.tools.execute(user.id, call.name, call.arguments)
                    tool_audit.append({"tool": call.name, "ok": result.get("ok", False)})
                    messages.append(
                        {
                            "role": "user",
                            "tool_result": {
                                "name": call.name,
                                "result": result,
                            },
                        }
                    )

            fallback = "Mình đã tra cứu nhiều bước nhưng chưa tổng hợp xong. Bạn vui lòng hỏi cụ thể hơn nhé."
            self.memory.add_message(
                user.id,
                role="assistant",
                content=fallback,
                channel=channel,
                tool_calls=tool_audit or None,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
            )
            return AgentReply(
                reply_text=fallback,
                tools_used=tools_used,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                degraded=True,
            )

        except LLMRateLimitError:
            logger.warning("Gemini Free Tier quota/rate limit reached for user_id=%s", user.id)
            rate_msg = (
                "Hệ thống AI (Gemini Free Tier) hiện đang chạm giới hạn lượt gọi tạm thời. "
                "Dữ liệu lịch học và deadline của bạn vẫn an toàn — bạn có thể xem trực tiếp trên Dashboard hoặc thử lại sau vài phút."
            )
            self.memory.add_message(user.id, role="assistant", content=rate_msg, channel=channel)
            return AgentReply(reply_text=rate_msg, tools_used=tools_used, degraded=True)

        except LLMProviderError as exc:
            logger.error("LLM provider error for user_id=%s: %s", user.id, exc)
            err_msg = (
                "Hiện chưa thể kết nối tới dịch vụ AI. Bạn vui lòng kiểm tra cấu hình GEMINI_API_KEY hoặc thử lại sau."
            )
            self.memory.add_message(user.id, role="assistant", content=err_msg, channel=channel)
            return AgentReply(reply_text=err_msg, tools_used=tools_used, degraded=True)
