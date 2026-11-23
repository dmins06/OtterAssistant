import hmac


def verify_telegram_webhook_secret(
    secret_header: str | None,
    expected_secret: str,
) -> bool:
    """
    Verify Telegram's `X-Telegram-Bot-Api-Secret-Token` header using constant-time comparison.
    """
    if not secret_header or not expected_secret:
        return False
    return hmac.compare_digest(secret_header.strip(), expected_secret.strip())
