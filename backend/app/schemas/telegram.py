from datetime import datetime

from pydantic import BaseModel


class TelegramLinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    bot_username: str | None = None
    instruction: str


class TelegramStatusResponse(BaseModel):
    linked: bool
    display_name: str | None = None
    bot_username: str | None = None
    last_interaction_at: datetime | None = None
