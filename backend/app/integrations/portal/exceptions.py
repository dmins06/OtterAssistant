class PortalIntegrationError(Exception):
    """Base exception for Portal UTH integration errors."""

    def __init__(self, message: str, error_code: str = "PORTAL_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class PortalReauthRequiredError(PortalIntegrationError):
    """Raised when Portal session cannot be refreshed and user must re-authenticate."""

    def __init__(self, message: str = "Portal UTH requires re-authentication.", error_code: str = "REAUTH_REQUIRED") -> None:
        super().__init__(message, error_code=error_code)


class PortalHTTPStatusError(PortalIntegrationError):
    """Typed HTTP error raised by PortalClient with status code and optional Retry-After."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str | None = None,
        retry_after_seconds: float | None = None,
        is_csrf_error: bool = False,
    ) -> None:
        super().__init__(message, error_code=error_code or f"HTTP_{status_code}")
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.is_csrf_error = is_csrf_error
