import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Patterns that must never leak into logs (JWTs, Bearer tokens, API keys, passwords)
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+", re.IGNORECASE), r"\1[REDACTED_JWT]"),
    (re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"), "[REDACTED_JWT]"),
    (re.compile(r'("?(?:password|passwd|secret|api_key|access_token|refresh_token|authorization|cookie|wstoken)"?\s*[:=]\s*"?)([^",\s}]+)', re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r'([?&](?:token|access_token|wstoken|jwt|auth|key|secret|sesskey)=)([^&\s"\']+)', re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(/bot)(\d+:[A-Za-z0-9_\-]+)"), r"\1[REDACTED_TG_TOKEN]"),
]


def redact_sensitive(text: str) -> str:
    """Mask JWTs, tokens, passwords, and API keys from any log message."""
    sanitized = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


class SensitiveDataFilter(logging.Filter):
    """Logging filter that scrubs sensitive credentials from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Emit structured JSON log entries in UTC."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())
    handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(handler)
    # Prevent httpx from logging full request URLs (which include Telegram bot tokens) at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
