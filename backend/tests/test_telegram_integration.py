from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.llm_provider import LLMProvider, LLMResponse, ToolCallRequest
from app.api.routes.agent import get_llm_provider
from app.api.routes.telegram import get_telegram_client
from app.core.config import Settings
from app.integrations.telegram.client import MockTelegramBotClient
from app.models.enums import NotificationStatus
from app.models.user import User
from tests.conftest import auth_headers


class _FakeTelegramAgentLLM(LLMProvider):
    def __init__(self) -> None:
        self._step = 0

    def generate(self, system_prompt, messages, tool_declarations):  # type: ignore[no-untyped-def]
        self._step += 1
        if self._step == 1:
            return LLMResponse(
                tool_calls=[ToolCallRequest(name="get_today_schedule", arguments={})],
                prompt_tokens=80,
                completion_tokens=15,
            )
        return LLMResponse(
            text="Hôm nay bạn không có lịch học nào trên Portal UTH.",
            prompt_tokens=120,
            completion_tokens=20,
        )


def test_telegram_webhook_secret_token_rejects_invalid_and_accepts_valid(
    client: TestClient,
) -> None:
    mock_tg = MockTelegramBotClient()
    client.app.dependency_overrides[get_telegram_client] = lambda: mock_tg  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_llm_provider] = lambda: _FakeTelegramAgentLLM()  # type: ignore[attr-defined]

    update_payload = {
        "update_id": 10001,
        "message": {
            "chat": {"id": 99001},
            "from": {"id": 99001, "username": "uth_student"},
            "text": "/start",
        },
    }
    fake_settings = Settings(telegram_webhook_secret="my-tg-secret-token")
    with patch("app.api.routes.telegram.get_settings", return_value=fake_settings):
        bad_resp = client.post(
            "/api/v1/webhooks/telegram",
            json=update_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert bad_resp.status_code == 401

        ok_resp = client.post(
            "/api/v1/webhooks/telegram",
            json=update_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "my-tg-secret-token"},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["command"] == "start"
        assert len(mock_tg.sent_messages) == 1


def test_telegram_one_time_linking_agent_reply_and_unlink(
    client: TestClient,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    mock_tg = MockTelegramBotClient(blocked_chat_ids={"777777"})
    client.app.dependency_overrides[get_telegram_client] = lambda: mock_tg  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_llm_provider] = lambda: _FakeTelegramAgentLLM()  # type: ignore[attr-defined]

    # 1. User A generates a one-time linking code on Dashboard
    code_resp = client.post("/api/v1/integrations/telegram/link-code", headers=auth_headers(user_a))
    assert code_resp.status_code == 200
    link_code = code_resp.json()["code"]
    assert link_code.startswith("OTTER-")

    # 2. Unlinked Telegram chat sends message -> prompted to link, never accesses User A data
    unlinked_resp = client.post(
        "/api/v1/webhooks/telegram",
        json={
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},
                "from": {"id": 123456, "username": "minh_uth"},
                "text": "Hôm nay học gì?",
            },
        },
    )
    assert unlinked_resp.status_code == 200
    assert unlinked_resp.json()["linked"] is False

    # 3. User sends /link <CODE> -> links account & consumes code (single-use)
    link_resp = client.post(
        "/api/v1/webhooks/telegram",
        json={
            "update_id": 2,
            "message": {
                "chat": {"id": 123456},
                "from": {"id": 123456, "username": "minh_uth"},
                "text": f"/link {link_code}",
            },
        },
    )
    assert link_resp.status_code == 200
    assert link_resp.json()["linked"] is True
    assert link_resp.json()["user_id"] == user_a.id

    # Reusing the same code fails
    reuse_resp = client.post(
        "/api/v1/webhooks/telegram",
        json={
            "update_id": 3,
            "message": {
                "chat": {"id": 999999},
                "from": {"id": 999999, "username": "other"},
                "text": f"/link {link_code}",
            },
        },
    )
    assert reuse_resp.json()["linked"] is False

    # User A status shows linked + @minh_uth; User B remains unlinked
    status_a = client.get("/api/v1/integrations/telegram/status", headers=auth_headers(user_a)).json()
    assert status_a["linked"] is True
    assert status_a["display_name"] == "@minh_uth"
    status_b = client.get("/api/v1/integrations/telegram/status", headers=auth_headers(user_b)).json()
    assert status_b["linked"] is False

    # 4. Linked user asks schedule via Telegram -> routed to OtterAssistantAgent & replied
    chat_resp = client.post(
        "/api/v1/webhooks/telegram",
        json={
            "update_id": 4,
            "message": {
                "chat": {"id": 123456},
                "from": {"id": 123456, "username": "minh_uth"},
                "text": "Hôm nay mình học môn gì?",
            },
        },
    )
    assert chat_resp.status_code == 200
    assert chat_resp.json()["delivery_status"] == "SENT"
    assert chat_resp.json()["tools_used"] == ["get_today_schedule"]
    assert "không có lịch học" in mock_tg.sent_messages[-1]["text"]

    # 5. Blocked bot returns POLICY_RESTRICTED cleanly
    blocked_res = mock_tg.send_message("777777", "Test")
    assert blocked_res.ok is False
    assert blocked_res.status == NotificationStatus.POLICY_RESTRICTED

    # 6. /unlink deactivates linking
    client.post(
        "/api/v1/webhooks/telegram",
        json={
            "update_id": 5,
            "message": {
                "chat": {"id": 123456},
                "from": {"id": 123456, "username": "minh_uth"},
                "text": "/unlink",
            },
        },
    )
    assert (
        client.get("/api/v1/integrations/telegram/status", headers=auth_headers(user_a)).json()["linked"]
        is False
    )
