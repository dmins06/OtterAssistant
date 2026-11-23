class LMSIntegrationError(Exception):
    """Base exception for UTH LMS integration errors."""

    def __init__(self, message: str, error_code: str = "LMS_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class LMSAuthRequiredError(LMSIntegrationError):
    """Raised when LMS is disconnected or credentials are rejected."""

    def __init__(self, message: str = "UTH LMS requires authentication.", error_code: str = "LMS_AUTH_REQUIRED") -> None:
        super().__init__(message, error_code=error_code)


class LMSChallengeBlockedError(LMSIntegrationError):
    """Raised when LMS is gated by Cloudflare/CAPTCHA anti-bot challenge (never bypassed)."""

    def __init__(
        self,
        message: str = "UTH LMS request was blocked by an anti-bot/Cloudflare challenge.",
        error_code: str = "LMS_ANTI_BOT_CHALLENGE",
    ) -> None:
        super().__init__(message, error_code=error_code)
