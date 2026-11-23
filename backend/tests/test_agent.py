from datetime import date, time
import logging
from typing import Any

from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
import pytest
from sqlalchemy.orm import Session

from app.agents.assistant import OtterAssistantAgent
from app.agents.llm_provider import (
    GeminiLLMProvider,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    ToolCallRequest,
)
from app.agents.memory import ConversationMemoryManager
from app.api.routes.agent import get_llm_provider
from app.models.enums import SessionPeriod
from app.models.user import User
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService
from app.tools.academic_tools import build_academic_tool_registry
from tests.conftest import auth_headers


class ScriptedLLMProvider(LLMProvider):
    """Deterministic fake LLM provider to test multi-step Tool Calling without network."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.received_messages: list[list[dict[str, Any]]] = []

    def generate(
        self,
        system_prompt: str,  # noqa: ARG002
        messages: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],  # noqa: ARG002
    ) -> LLMResponse:
        self.received_messages.append(list(messages))
        return self._responses.pop(0)


def test_tool_registry_contains_all_section_8_tools_and_enforces_user_isolation(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    subj = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="012012710505", name="Học sâu"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=subj.id,
            source_event_id="evt-agent-1",
            class_date=date(2026, 12, 12),
            weekday=7,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="C007",
            campus="CS1",
        ),
    )

    registry = build_academic_tool_registry(db_session)
    assert len(registry.list_declarations()) == 24

    # User A sees their class
    res_a = registry.execute(user_a.id, "get_schedule_by_date", {"target_date": "2026-12-12"})
    assert res_a["ok"] is True
    assert len(res_a["data"]["classes"]) == 1
    assert res_a["data"]["classes"][0]["room"] == "C007"

    # User B sees NO classes on the same date (strict user isolation)
    res_b = registry.execute(user_b.id, "get_schedule_by_date", {"target_date": "2026-12-12"})
    assert res_b["ok"] is True
    assert res_b["data"]["classes"] == []

    # Unknown tool & invalid arguments return structured errors without raising
    unknown = registry.execute(user_a.id, "execute_raw_sql", {"sql": "SELECT * FROM users"})
    assert unknown["ok"] is False
    assert "Unknown tool" in unknown["error"]

    invalid = registry.execute(user_a.id, "get_upcoming_deadlines", {"days": -5})
    assert invalid["ok"] is False


def test_agent_multi_step_tool_calling_and_conversation_persistence(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    subj = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="012012710505", name="Học sâu"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=subj.id,
            source_event_id="evt-dl-12",
            class_date=date(2026, 12, 12),
            weekday=7,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="C007",
            campus="CS1",
        ),
    )

    llm = ScriptedLLMProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        name="get_schedule_by_date",
                        arguments={"target_date": "2026-12-12"},
                    )
                ],
                prompt_tokens=120,
                completion_tokens=20,
            ),
            LLMResponse(
                text="Ngày 12/12/2026 bạn có môn Học sâu (Sáng, Ca 2, Tiết 4-6, 09:25-11:55) tại phòng C007 - CS1.",
                prompt_tokens=210,
                completion_tokens=45,
            ),
        ]
    )
    agent = OtterAssistantAgent(db_session, llm_provider=llm)
    reply = agent.chat(user_a, "Ngày 12/12/2026 mình học môn gì?")

    assert reply.degraded is False
    assert reply.tools_used == ["get_schedule_by_date"]
    assert "Học sâu" in reply.reply_text
    assert reply.prompt_tokens == 330
    assert reply.completion_tokens == 65

    history = ConversationMemoryManager(db_session).get_recent_messages(user_a.id)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"


def test_agent_handles_rate_limit_gracefully_and_gemini_exponential_backoff(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users

    # 1. Verify GeminiLLMProvider exponential backoff on 429
    slept: list[float] = []

    class FlakyGemini(GeminiLLMProvider):
        def __init__(self) -> None:
            super().__init__(
                api_key="test-key",
                max_retries=2,
                base_delay_seconds=1.0,
                sleep_fn=lambda d: slept.append(d),
            )
            self.attempts = 0

        def _call_sdk(self, system_prompt, messages, tool_declarations):  # type: ignore[no-untyped-def]
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return LLMResponse(text="Recovered after backoff", prompt_tokens=10, completion_tokens=5)

    flaky = FlakyGemini()
    res = flaky.generate("sys", [{"role": "user", "content": "hi"}], [])
    assert res.text == "Recovered after backoff"
    assert slept == [1.0, 2.0]

    # 2. Verify Agent degrades gracefully without losing user message when quota exhausted
    class ExhaustedLLM(LLMProvider):
        def generate(self, system_prompt, messages, tool_declarations):  # type: ignore[no-untyped-def]
            raise LLMRateLimitError("Quota exceeded")

    agent = OtterAssistantAgent(db_session, llm_provider=ExhaustedLLM())
    reply = agent.chat(user_a, "Hôm nay có deadline không?")
    assert reply.degraded is True
    assert "Gemini Free Tier" in reply.reply_text
    assert len(ConversationMemoryManager(db_session).get_recent_messages(user_a.id)) == 2


def test_agent_chat_api_endpoint(
    client: TestClient,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    scripted = ScriptedLLMProvider(
        [
            LLMResponse(
                tool_calls=[ToolCallRequest(name="get_daily_summary", arguments={})],
                prompt_tokens=100,
                completion_tokens=15,
            ),
            LLMResponse(
                text="Hôm nay bạn không có lịch học và chưa có deadline nào được đồng bộ.",
                prompt_tokens=180,
                completion_tokens=30,
            ),
        ]
    )
    client.app.dependency_overrides[get_llm_provider] = lambda: scripted  # type: ignore[attr-defined]

    resp = client.post(
        "/api/v1/agent/chat",
        headers=auth_headers(user_a),
        json={"message": "Tổng hợp hôm nay giúp mình"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools_used"] == ["get_daily_summary"]
    assert body["prompt_tokens"] == 280
    assert body["degraded"] is False

    hist = client.get("/api/v1/agent/history", headers=auth_headers(user_a))
    assert hist.status_code == 200
    assert len(hist.json()) == 2


@pytest.mark.parametrize(
    ("exc_cls", "http_status", "api_status", "expected_category", "raw_message"),
    [
        (
            genai_errors.ClientError,
            400,
            "INVALID_ARGUMENT",
            "bad_request",
            "API key not valid for AIzaSyFakeSecretGeminiKey1234567890",
        ),
        (
            genai_errors.ClientError,
            401,
            "UNAUTHENTICATED",
            "unauthorized",
            "Request is missing required authentication credential.",
        ),
        (
            genai_errors.ClientError,
            403,
            "PERMISSION_DENIED",
            "forbidden",
            "Generative Language API has not been used in project.",
        ),
        (
            genai_errors.ClientError,
            404,
            "NOT_FOUND",
            "not_found",
            "models/gemini-2.5-flash is not found for API version v1beta.",
        ),
        (
            genai_errors.ServerError,
            503,
            "UNAVAILABLE",
            "server_error",
            "The model is overloaded. Please try again later.",
        ),
    ],
)
def test_gemini_error_logging_classifies_status_and_redacts_api_key(
    caplog: pytest.LogCaptureFixture,
    exc_cls: type[genai_errors.APIError],
    http_status: int,
    api_status: str,
    expected_category: str,
    raw_message: str,
) -> None:
    fake_key = "AIzaSyFakeSecretGeminiKey1234567890"

    class FailingGemini(GeminiLLMProvider):
        def _call_sdk(self, system_prompt, messages, tool_declarations):  # type: ignore[no-untyped-def]
            raise exc_cls(
                http_status,
                {"error": {"code": http_status, "status": api_status, "message": raw_message}},
            )

    provider = FailingGemini(api_key=fake_key, model="gemini-2.5-flash", max_retries=0)
    with caplog.at_level(logging.ERROR, logger="app.agents.llm_provider"):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("sys", [{"role": "user", "content": "hi"}], [])

    err_str = str(exc_info.value)
    assert f"category={expected_category}" in err_str
    assert f"http_status={http_status}" in err_str
    assert f"error_code={api_status}" in err_str
    assert fake_key not in err_str

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert f"category={expected_category}" in log_output
    assert f"http_status={http_status}" in log_output
    assert f"error_code={api_status}" in log_output
    assert f"exception={exc_cls.__name__}" in log_output
    assert fake_key not in log_output


def test_gemini_429_exhausted_logs_rate_limited_and_preserves_fallback(
    db_session: Session,
    two_users: tuple[User, User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_a, _ = two_users
    fake_key = "AIzaSyFakeSecretGeminiKey9999999999"
    slept: list[float] = []

    class RateLimitedGemini(GeminiLLMProvider):
        def _call_sdk(self, system_prompt, messages, tool_declarations):  # type: ignore[no-untyped-def]
            raise genai_errors.ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": f"Quota exceeded for key={fake_key}",
                    }
                },
            )

    provider = RateLimitedGemini(
        api_key=fake_key,
        model="gemini-2.5-flash",
        max_retries=1,
        base_delay_seconds=0.5,
        sleep_fn=lambda d: slept.append(d),
    )
    agent = OtterAssistantAgent(db_session, llm_provider=provider)
    with caplog.at_level(logging.WARNING, logger="app.agents"):
        reply = agent.chat(user_a, "Hôm nay học gì?")

    assert slept == [0.5]
    assert reply.degraded is True
    assert "Gemini Free Tier" in reply.reply_text

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert "category=rate_limited" in log_output
    assert "http_status=429" in log_output
    assert "error_code=RESOURCE_EXHAUSTED" in log_output
    assert "exception=ClientError" in log_output
    assert fake_key not in log_output
