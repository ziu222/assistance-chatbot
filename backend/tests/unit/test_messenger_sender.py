"""Kiểm thử `MessengerSender` dùng `httpx.MockTransport` (không gọi mạng thật)."""

import json

import httpx

from app.modules.outbound.infrastructure.messenger_sender import MessengerSender


async def test_messenger_sender_success():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"recipient_id": "psid_1", "message_id": "mid.$cAA123"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = MessengerSender("https://graph.facebook.com/v21.0", client=client)
        outcome = await sender.send(
            recipient_id="psid_1",
            text="Dạ shop mở cửa 8h-21h ạ.",
            access_token="EAAB_token_xyz",
        )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://graph.facebook.com/v21.0/me/messages"
    assert captured["auth"] == "Bearer EAAB_token_xyz"
    assert captured["body"] == {
        "recipient": {"id": "psid_1"},
        "messaging_type": "RESPONSE",
        "message": {"text": "Dạ shop mở cửa 8h-21h ạ."},
    }
    assert outcome.ok is True
    assert outcome.retryable is False
    assert outcome.provider_message_id == "mid.$cAA123"


async def test_messenger_sender_error_classification_and_timeout():
    for status, expected_retryable in [(400, False), (429, True), (500, True)]:

        def handler(_request: httpx.Request, code: int = status) -> httpx.Response:
            return httpx.Response(code, json={"error": {"message": f"err {code}"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = MessengerSender("https://graph.facebook.com/v21.0", client=client)
            outcome = await sender.send(
                recipient_id="psid_1", text="hi", access_token="EAAB_token_xyz"
            )
        assert outcome.ok is False
        assert outcome.retryable is expected_retryable

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        sender = MessengerSender("https://graph.facebook.com/v21.0", client=client)
        outcome = await sender.send(recipient_id="psid_1", text="hi", access_token="EAAB_token_xyz")
    assert outcome.ok is False
    assert outcome.retryable is True
    assert outcome.error == "ReadTimeout"
