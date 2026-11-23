from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.llm_provider import LLMProvider
from app.api.dependencies.auth import get_current_user
from app.api.routes.agent import get_llm_provider
from app.core.config import get_settings
from app.database.session import get_db
from app.integrations.telegram.client import TelegramBotClient, TelegramProvider
from app.integrations.telegram.linking import TelegramAccountLinkingService
from app.integrations.telegram.message_handler import TelegramMessageHandler
from app.integrations.telegram.webhook import verify_telegram_webhook_secret
from app.models.user import User
from app.schemas.telegram import TelegramLinkCodeResponse, TelegramStatusResponse

router = APIRouter(tags=["telegram-bot"])


def get_telegram_client() -> TelegramProvider:
    return TelegramBotClient()


@router.post("/webhooks/telegram")
def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
    db: Session = Depends(get_db),
    telegram_client: TelegramProvider = Depends(get_telegram_client),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> dict[str, Any]:
    settings = get_settings()
    if settings.telegram_webhook_secret:
        if not verify_telegram_webhook_secret(
            x_telegram_bot_api_secret_token,
            settings.telegram_webhook_secret,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Telegram webhook secret token.",
            )

    handler = TelegramMessageHandler(
        db=db,
        telegram_client=telegram_client,
        llm_provider=llm_provider,
    )
    return handler.handle_update(update)


@router.get("/integrations/telegram/status", response_model=TelegramStatusResponse)
def get_telegram_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelegramStatusResponse:
    settings = get_settings()
    bot_username = settings.telegram_bot_username or None
    linking = TelegramAccountLinkingService(db).get_user_linking(current_user.id)
    if linking is None:
        return TelegramStatusResponse(
            linked=False,
            display_name=None,
            bot_username=bot_username,
            last_interaction_at=None,
        )
    return TelegramStatusResponse(
        linked=True,
        display_name=linking.display_name,
        bot_username=bot_username,
        last_interaction_at=linking.last_interaction_at,
    )


@router.post("/integrations/telegram/link-code", response_model=TelegramLinkCodeResponse)
def create_telegram_link_code(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelegramLinkCodeResponse:
    settings = get_settings()
    bot_username = settings.telegram_bot_username or None
    code, expires_at = TelegramAccountLinkingService(db).generate_link_code(current_user.id)
    bot_hint = f"@{bot_username}" if bot_username else "Otter Assistant Telegram Bot"
    return TelegramLinkCodeResponse(
        code=code,
        expires_at=expires_at,
        bot_username=bot_username,
        instruction=f"Mở {bot_hint} trên Telegram và gửi '/link {code}' trong vòng 10 phút.",
    )


@router.delete("/integrations/telegram/link", response_model=TelegramStatusResponse)
def unlink_telegram(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelegramStatusResponse:
    settings = get_settings()
    TelegramAccountLinkingService(db).unlink_user(current_user.id)
    return TelegramStatusResponse(
        linked=False,
        display_name=None,
        bot_username=settings.telegram_bot_username or None,
        last_interaction_at=None,
    )
