"""Kiểm thử webhook Facebook Messenger (O5.2): challenge GET, HMAC POST, idempotency."""

import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.modules.integration.infrastructure.messenger.connections import verify_token_hash
from app.shared.infrastructure.crypto import encrypt_json

from .factories import make_tenant

TEST_CRED_KEY = Fernet.generate_key().decode("ascii")
APP_SECRET = "meta-app-secret-012345"
VERIFY_TOKEN = "meta-verify-token-xyz"
PAGE_ACCESS_TOKEN = "EAAB-page-access-token"
PAGE_ID = "100200300400"


@pytest.fixture
async def msg_client(app_client):
    return await app_client("https://api.test", channel_credentials_key=TEST_CRED_KEY)


async def _seed_messenger_connection(
    session_factory,
    *,
    page_id: str = PAGE_ID,
    status: str = "active",
    tenant_status: str = "active",
    with_hash: bool = True,
):
    encrypted = encrypt_json(
        {
            "app_secret": APP_SECRET,
            "verify_token": VERIFY_TOKEN,
            "page_access_token": PAGE_ACCESS_TOKEN,
        },
        TEST_CRED_KEY,
    )
    cfg = {"verify_token_hash": verify_token_hash(VERIFY_TOKEN)} if with_hash else {}
    async with session_factory() as s, s.begin():
        ctx = await make_tenant(s)
        if tenant_status != "active":
            await s.execute(
                text("UPDATE tenants SET status = :st WHERE id = :t"),
                {"st": tenant_status, "t": ctx.tenant_id},
            )
        conn_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO channel_connections "
                "(id, tenant_id, provider, external_account_id, status, config, "
                " credentials_encrypted) "
                "VALUES (:id, :t, 'messenger', :ext, :st, CAST(:cfg AS jsonb), :cred)"
            ),
            {
                "id": conn_id,
                "t": ctx.tenant_id,
                "ext": page_id,
                "st": status,
                "cfg": json.dumps(cfg),
                "cred": encrypted,
            },
        )
    return ctx, conn_id


def _sign(raw_body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _meta_payload(
    *,
    page_id: str = PAGE_ID,
    psid: str = "psid_cust_1",
    mid: str = "m_mid_1",
    text_: str = "Chào shop ạ!",
    ts_ms: int = 1_732_680_000_000,
) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": ts_ms,
                "messaging": [
                    {
                        "sender": {"id": psid},
                        "recipient": {"id": page_id},
                        "timestamp": ts_ms,
                        "message": {"mid": mid, "text": text_},
                    }
                ],
            }
        ],
    }


async def test_get_webhook_challenge_verifies_token_and_rejects_invalid(
    msg_client, session_factory
):
    await _seed_messenger_connection(session_factory, with_hash=True)

    ok = await msg_client.get(
        "/webhooks/messenger",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "CHALLENGE_123",
        },
    )
    assert ok.status_code == 200
    assert ok.text == "CHALLENGE_123"

    wrong_tok = await msg_client.get(
        "/webhooks/messenger",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "CHALLENGE_123",
        },
    )
    assert wrong_tok.status_code == 403

    wrong_mode = await msg_client.get(
        "/webhooks/messenger",
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "CHALLENGE_123",
        },
    )
    assert wrong_mode.status_code == 403


async def test_post_webhook_invalid_or_missing_signature_writes_nothing(
    msg_client, session_factory
):
    await _seed_messenger_connection(session_factory)
    raw = json.dumps(_meta_payload()).encode("utf-8")

    missing = await msg_client.post(
        "/webhooks/messenger",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert missing.status_code == 403

    bad_sig = await msg_client.post(
        "/webhooks/messenger",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, "wrong-secret"),
        },
    )
    assert bad_sig.status_code == 403

    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM customers"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM conversations"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM messages"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM outbox_events"))).scalar_one() == 0


async def test_post_webhook_creates_conversation_message_outbox_and_is_idempotent(
    msg_client, session_factory
):
    ctx, conn_id = await _seed_messenger_connection(session_factory)
    raw = json.dumps(_meta_payload(text_="  Em muốn   hỏi giá\x00 ")).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(raw)}

    first = await msg_client.post("/webhooks/messenger", content=raw, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"ok": True, "received": 1}

    # Meta retry cùng `mid` ⇒ idempotent, vẫn chỉ có 1 message.
    retry = await msg_client.post("/webhooks/messenger", content=raw, headers=headers)
    assert retry.status_code == 200
    assert retry.json() == {"ok": True, "received": 1}

    async with session_factory() as s:
        cust_count = (
            await s.execute(
                text("SELECT count(*) FROM customers WHERE tenant_id = :t"), {"t": ctx.tenant_id}
            )
        ).scalar_one()
        ident = (
            await s.execute(
                text(
                    "SELECT provider, scoped_external_id FROM channel_identities "
                    "WHERE tenant_id = :t AND channel_connection_id = :c"
                ),
                {"t": ctx.tenant_id, "c": conn_id},
            )
        ).one()
        conv = (
            await s.execute(
                text(
                    "SELECT id, last_sequence, unread_count FROM conversations "
                    "WHERE tenant_id = :t AND channel_connection_id = :c"
                ),
                {"t": ctx.tenant_id, "c": conn_id},
            )
        ).one()
        msgs = (
            await s.execute(
                text(
                    "SELECT direction, sender_type, external_message_id, content, sequence "
                    "FROM messages WHERE tenant_id = :t AND conversation_id = :c"
                ),
                {"t": ctx.tenant_id, "c": conv.id},
            )
        ).all()
        outbox_types = (
            (
                await s.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE tenant_id = :t AND aggregate_id = :c ORDER BY id"
                    ),
                    {"t": ctx.tenant_id, "c": conv.id},
                )
            )
            .scalars()
            .all()
        )

    assert cust_count == 1
    assert ident.provider == "messenger"
    assert ident.scoped_external_id == "psid_cust_1"
    assert conv.last_sequence == 1
    assert conv.unread_count == 1
    assert len(msgs) == 1
    assert msgs[0].direction == "inbound"
    assert msgs[0].sender_type == "customer"
    assert msgs[0].external_message_id == "m_mid_1"
    assert msgs[0].content == "Em muốn   hỏi giá"
    assert msgs[0].sequence == 1
    assert outbox_types == ["conversation.created", "message.created"]


async def test_post_webhook_ignores_echo_and_non_text_events(msg_client, session_factory):
    await _seed_messenger_connection(session_factory)
    payload = {
        "object": "page",
        "entry": [
            {
                "id": PAGE_ID,
                "time": 1_732_680_000_000,
                "messaging": [
                    {
                        "sender": {"id": PAGE_ID},
                        "recipient": {"id": "psid_1"},
                        "timestamp": 1_732_680_000_000,
                        "message": {"mid": "m_echo", "is_echo": True, "text": "Echo"},
                    },
                    {
                        "sender": {"id": "psid_1"},
                        "recipient": {"id": PAGE_ID},
                        "timestamp": 1_732_680_001_000,
                        "delivery": {"mids": ["m_echo"], "watermark": 1_732_680_001_000},
                    },
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode("utf-8")
    r = await msg_client.post(
        "/webhooks/messenger",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(raw)},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "received": 0}
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM messages"))).scalar_one() == 0
