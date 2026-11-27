"""Webhook công khai cho Facebook Messenger (O5.2, ADR-0036).

Chỉ xác minh chữ ký HMAC-SHA256 / verify_token → chuẩn hóa `NormalizedChannelEvent` →
gọi `ReceiveInboundMessage`. Không đặt logic nghiệp vụ và không bao giờ log body, token, secret.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import PlainTextResponse

from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.modules.integration.domain.messenger import (
    parse_webhook_payload,
    verify_signature,
)
from app.modules.integration.domain.normalization import InvalidInboundMessage, normalize_text
from app.modules.integration.infrastructure.messenger import connections
from app.shared.application.errors import TransientDatabaseError
from app.shared.infrastructure.metrics import INBOUND_MESSAGES
from app.shared.presentation.http_errors import ApiError

router = APIRouter()


@router.get("/webhooks/messenger")
async def verify_messenger_webhook(
    request: Request,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Xác minh challenge từ Meta khi đăng ký webhook Messenger."""
    if hub_mode != "subscribe" or not hub_verify_token or hub_challenge is None:
        raise ApiError(403, "verification_failed", "Invalid webhook verification parameters")

    credentials_key = request.app.state.settings.channel_credentials_key
    if not credentials_key:
        raise ApiError(403, "verification_failed", "Webhook verification failed")

    async with request.app.state.session_factory() as s:
        conn = await connections.by_verify_token(s, hub_verify_token, credentials_key)
    if conn is None:
        raise ApiError(403, "verification_failed", "Webhook verification failed")
    return PlainTextResponse(hub_challenge, status_code=200)


@router.post("/webhooks/messenger")
async def receive_messenger_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    """Nhận sự kiện webhook Messenger, kiểm HMAC-SHA256 theo `page_id` và đưa vào pipeline."""
    if not x_hub_signature_256:
        raise ApiError(403, "invalid_signature", "Missing webhook signature")

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_payload", "Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_payload", "Webhook payload must be a JSON object")

    if payload.get("object") != "page":
        return {"ok": True, "received": 0}

    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise ApiError(400, "invalid_payload", "Webhook entry must be a list")

    page_ids = list(
        dict.fromkeys(
            str(entry.get("id") or "").strip()
            for entry in entries
            if isinstance(entry, dict) and str(entry.get("id") or "").strip()
        )
    )
    if not page_ids:
        return {"ok": True, "received": 0}

    credentials_key = request.app.state.settings.channel_credentials_key
    if not credentials_key:
        raise ApiError(403, "invalid_signature", "Invalid webhook signature")

    factory = request.app.state.session_factory
    conns: dict[str, connections.MessengerConnection] = {}
    async with factory() as s:
        for page_id in page_ids:
            conn = await connections.by_page_id(s, page_id, credentials_key)
            if conn is None or not verify_signature(raw_body, x_hub_signature_256, conn.app_secret):
                raise ApiError(403, "invalid_signature", "Invalid webhook signature")
            conns[page_id] = conn

    inbound_items = parse_webhook_payload(payload)
    trace_id = request.state.trace_id
    normalized_events: list[NormalizedChannelEvent] = []
    for item in inbound_items:
        conn = conns.get(item.page_id)
        if conn is None:
            continue
        try:
            clean_text = normalize_text(item.text)
        except InvalidInboundMessage as exc:
            INBOUND_MESSAGES.labels(result="rejected", reason="invalid_request").inc()
            raise ApiError(422, "invalid_request", str(exc)) from exc
        normalized_events.append(
            NormalizedChannelEvent(
                tenant_id=conn.tenant_id,
                channel_connection_id=conn.id,
                provider="messenger",
                external_message_id=item.message_id,
                external_customer_id=item.sender_psid,
                direction="inbound",
                message_type="text",
                text=clean_text,
                occurred_at=item.occurred_at,
                trace_id=trace_id,
            )
        )

    use_case = ReceiveInboundMessage(lambda: SqlConversationUnitOfWork(factory))
    received = 0
    for event in normalized_events:
        try:
            result = await use_case(event)
        except TransientDatabaseError as exc:
            raise ApiError(503, "busy", "Please retry", {"Retry-After": "1"}) from exc
        INBOUND_MESSAGES.labels(
            result="created" if result.created else "duplicate", reason=""
        ).inc()
        received += 1

    return {"ok": True, "received": received}
