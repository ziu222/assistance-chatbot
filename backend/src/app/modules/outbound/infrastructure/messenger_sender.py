"""Adapter gửi tin văn bản qua Facebook Messenger Graph Send API (O5.3)."""

import httpx

from app.modules.outbound.domain.delivery import DeliveryOutcome, classify_http_status

_SEND_TIMEOUT_SECONDS = 10.0


class MessengerSender:
    """Gọi `POST {base_url}/me/messages`; không bao giờ log token hay nội dung tin nhắn."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def send(self, *, recipient_id: str, text: str, access_token: str) -> DeliveryOutcome:
        url = f"{self._base_url}/me/messages"
        headers = {"Authorization": f"Bearer {access_token}"}
        payload = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }
        try:
            if self._client is not None:
                resp = await self._client.post(
                    url, headers=headers, json=payload, timeout=_SEND_TIMEOUT_SECONDS
                )
            else:
                async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_SECONDS) as client:
                    resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryOutcome(ok=False, retryable=True, error=type(exc).__name__)

        if 200 <= resp.status_code < 300:
            provider_message_id: str | None = None
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("message_id") is not None:
                    provider_message_id = str(data["message_id"])
            except ValueError:
                provider_message_id = None
            return DeliveryOutcome(
                ok=True, retryable=False, provider_message_id=provider_message_id
            )
        return classify_http_status(resp.status_code, resp.text)
