from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import time as time_module
from typing import Any

from app.core.config import get_settings
from app.core.logging import redact_sensitive

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """Base error raised by LLM providers."""


class LLMRateLimitError(LLMProviderError):
    """Raised when Gemini Free Tier quota / rate limit (429) is exhausted."""


@dataclass(frozen=True)
class ToolCallRequest:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(ABC):
    """
    Provider-agnostic LLM interface (Section 4.2).
    Designed so Google Gemini (Free Tier) and Ollama (local) can be swapped seamlessly.
    """

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LLMResponse:
        ...


class GeminiLLMProvider(LLMProvider):
    """Google Gemini API (Free Tier) provider using the official `google-genai` SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.temperature = temperature if temperature is not None else settings.gemini_temperature
        self.max_output_tokens = (
            max_output_tokens if max_output_tokens is not None else settings.gemini_max_output_tokens
        )
        self.max_retries = max_retries
        self.base_delay_seconds = base_delay_seconds
        self._sleep = sleep_fn
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if not self.api_key:
            raise LLMProviderError("GEMINI_API_KEY is not configured.")
        if self._client is None:
            from google import genai  # lazy import so unit tests run fast without network

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _call_sdk(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LLMResponse:
        from google.genai import types

        client = self._get_client()
        fn_decls = [
            types.FunctionDeclaration(
                name=decl["name"],
                description=decl["description"],
                parameters=decl["parameters"],
            )
            for decl in tool_declarations
        ]
        config = types.GenerateContentConfig(
            system_instruction=redact_sensitive(system_prompt),
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            tools=[types.Tool(function_declarations=fn_decls)] if fn_decls else None,
        )

        # Build sanitized contents list (never send raw credentials/JWTs to Gemini)
        contents: list[types.Content] = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            parts: list[types.Part] = []
            if msg.get("content"):
                parts.append(types.Part.from_text(text=redact_sensitive(str(msg["content"]))))
            if msg.get("tool_result"):
                tr = msg["tool_result"]
                parts.append(
                    types.Part.from_function_response(
                        name=tr["name"],
                        response={"result": tr["result"]},
                    )
                )
            if parts:
                contents.append(types.Content(role=role, parts=parts))

        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        tool_calls: list[ToolCallRequest] = []
        if getattr(response, "function_calls", None):
            for fc in response.function_calls:
                tool_calls.append(
                    ToolCallRequest(
                        name=str(fc.name),
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        completion_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        text = None
        if not tool_calls:
            text = getattr(response, "text", None)

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LLMResponse:
        delay = self.base_delay_seconds
        for attempt in range(self.max_retries + 1):
            try:
                return self._call_sdk(system_prompt, messages, tool_declarations)
            except LLMRateLimitError:
                if attempt >= self.max_retries:
                    raise
                logger.warning("Gemini rate limit hit; retrying in %.1fs (attempt %d)", delay, attempt + 1)
                self._sleep(delay)
                delay *= 2
            except Exception as exc:
                status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                err_text = str(exc).lower()
                is_rate_limit = status_code == 429 or "429" in err_text or "resource_exhausted" in err_text
                if is_rate_limit:
                    if attempt >= self.max_retries:
                        raise LLMRateLimitError("Gemini Free Tier rate limit / quota exceeded.") from exc
                    logger.warning("Gemini 429 rate limit; backing off %.1fs", delay)
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise LLMProviderError(f"Gemini API error: {type(exc).__name__}") from exc
        raise LLMRateLimitError("Gemini Free Tier rate limit exceeded.")
