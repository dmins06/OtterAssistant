from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.agents.assistant import OtterAssistantAgent
from app.agents.llm_provider import GeminiLLMProvider, LLMProvider
from app.agents.memory import ConversationMemoryManager
from app.api.dependencies.auth import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    ConversationMessageResponse,
)

router = APIRouter(prefix="/agent", tags=["ai-agent"])


def get_llm_provider() -> LLMProvider:
    return GeminiLLMProvider()


@router.post("/chat", response_model=AgentChatResponse)
def chat_with_agent(
    payload: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> AgentChatResponse:
    agent = OtterAssistantAgent(db=db, llm_provider=llm_provider)
    result = agent.chat(user=current_user, message=payload.message, channel=payload.channel)
    return AgentChatResponse(
        reply=result.reply_text,
        tools_used=result.tools_used,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        degraded=result.degraded,
    )


@router.get("/history", response_model=list[ConversationMessageResponse])
def get_chat_history(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationMessageResponse]:
    messages = ConversationMemoryManager(db).get_recent_messages(current_user.id, limit=limit)
    return [ConversationMessageResponse.model_validate(m) for m in messages]
