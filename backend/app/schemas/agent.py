from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    channel: str = Field(default="dashboard", max_length=32)


class AgentChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    prompt_tokens: int
    completion_tokens: int
    degraded: bool = False


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    role: str
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime
