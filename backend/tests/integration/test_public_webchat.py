"""API public của widget: token, origin, rate limit, cô lập visitor, idempotency qua HTTP."""

import json
import os
from uuid import uuid4

import httpx
import jwt
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from app.config.settings import Settings
from app.main import create_app
from app.shared.application.errors import TransientDatabaseError

from .factories import make_connection, make_tenant

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
WIDGET_ORIGIN = "http://widget.test"
HOST_SITE = "https://shop.vn"
SECRETS = {"k1": "s" * 32}


def make_settings(db_url: str, **over) -> Settings:
    defaults = {
        "database_url": db_url,
        "redis_url": TEST_REDIS_URL,
        "public_origin": WIDGET_ORIGIN,
        "webchat_token_secrets": SECRETS,
        "webchat_token_kid": "k1",
    }
    return Settings(**(defaults | over))


@pytest.fixture
async def client_for(migrated_db_url):
    r = aioredis.from_url(TEST_REDIS_URL)
    await r.flushdb()
    await r.aclose()
    opened = []

    async def factory(**over):
        app = create_app(make_settings(migrated_db_url, **over))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=WIDGET_ORIGIN)
        opened.append((c, lifespan))
        return c

    yield factory
    for c, lifespan in opened:
        await c.aclose()
        await lifespan.__aexit__(None, None, None)


@pytest.fixture
async def client(client_for):
    return await client_for()


async def connection(session_factory, *, origins=(HOST_SITE,), status="active", tenant="active"):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        cid = await make_connection(s, ctx)
        await s.execute(
            text(
                "UPDATE channel_connections SET status = :st, config = CAST(:cfg AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "id": cid,
                "st": status,
                "cfg": json.dumps(
                    {
                        "allowed_origins": list(origins),
                        "greeting": "Xin chào!",
                        "theme": {"color": "#0a7"},
                        "privacy_url": "https://shop.vn/privacy",
                        "internal_note": "không được lộ ra widget",
                    }
                ),
            },
        )
        await s.execute(
            text("UPDATE tenants SET status = :st WHERE id = :id"),
            {"id": ctx.tenant_id, "st": tenant},
        )
        key = (
            await s.execute(
                text("SELECT public_key FROM channel_connections WHERE id = :id"), {"id": cid}
            )
        ).scalar_one()
        await s.commit()
    return ctx, cid, key


async def open_session(client, key, token=None, origin=WIDGET_ORIGIN):
    body = {"public_key": key} | ({"token": token} if token else {})
    headers = {"Origin": origin} if origin else {}
    return await client.post("/public/webchat/v1/sessions", json=body, headers=headers)


async def token_for(client, key) -> str:
    r = await open_session(client, key)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def send(client, token, text_="xin chào", cmid=None):
    return await client.post(
        "/public/webchat/v1/messages",
        json={"client_message_id": cmid or str(uuid4()), "text": text_},
        headers=bearer(token),
    )


async def count(session_factory, table) -> int:
    async with session_factory() as s:
        return (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


# ---------- GET /w/{key} ----------


async def test_iframe_page_sets_frame_ancestors(client, session_factory):
    _, _, key = await connection(session_factory, origins=[HOST_SITE, "https://x.vn; script-src *"])
    r = await client.get(f"/w/{key}")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert f"frame-ancestors {HOST_SITE};" in csp + ";"
    assert "script-src *" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "kwargs", [{"origins": []}, {"status": "disabled"}, {"tenant": "suspended"}]
)
async def test_iframe_page_fails_closed(client, session_factory, kwargs):
    _, _, key = await connection(session_factory, **kwargs)
    assert (await client.get(f"/w/{key}")).status_code == 404


async def test_iframe_page_unknown_key(client):
    assert (await client.get("/w/wpk_nope")).status_code == 404


# ---------- sessions ----------


async def test_session_returns_token_and_public_config_without_db_writes(client, session_factory):
    _, _, key = await connection(session_factory)
    for _ in range(5):
        r = await open_session(client, key)
        assert r.status_code == 200
    body = r.json()
    assert body["config"] == {
        "greeting": "Xin chào!",
        "theme": {"color": "#0a7"},
        "privacy_url": "https://shop.vn/privacy",
    }
    assert body["token"] and body["expires_at"]
    assert await count(session_factory, "customers") == 0
    assert await count(session_factory, "channel_identities") == 0


async def test_session_renewal_keeps_visitor(client, session_factory):
    _, _, key = await connection(session_factory)
    first = await token_for(client, key)
    await send(client, first, "tin 1")

    renewed = (await open_session(client, key, token=first)).json()["token"]
    assert vid(renewed) == vid(first)
    msgs = (await client.get("/public/webchat/v1/messages", headers=bearer(renewed))).json()
    assert [m["text"] for m in msgs["messages"]] == ["tin 1"]


async def test_session_token_from_other_connection_gets_new_visitor(client, session_factory):
    _, _, key_a = await connection(session_factory)
    _, _, key_b = await connection(session_factory)
    token_a = await token_for(client, key_a)
    await send(client, token_a, "bí mật của A")

    token_b = (await open_session(client, key_b, token=token_a)).json()["token"]
    msgs = (await client.get("/public/webchat/v1/messages", headers=bearer(token_b))).json()
    assert msgs["messages"] == []
    assert vid(token_b) != vid(token_a)  # visitor_id không được mang sang connection khác


def vid(token: str) -> str:
    return jwt.decode(token, options={"verify_signature": False})["vid"]


@pytest.mark.parametrize("origin", [None, HOST_SITE, "https://evil.vn"])
async def test_session_rejects_foreign_origin(client, session_factory, origin):
    _, _, key = await connection(session_factory)
    r = await open_session(client, key, origin=origin)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_not_allowed"
    assert r.json()["trace_id"]


async def test_session_unknown_or_disabled_key(client, session_factory):
    _, _, key = await connection(session_factory, status="disabled")
    assert (await open_session(client, key)).status_code == 404
    assert (await open_session(client, "wpk_nope")).status_code == 404


async def test_session_rate_limited_per_ip(client, session_factory):
    _, _, key = await connection(session_factory)
    codes = [(await open_session(client, key)).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    r = await open_session(client, key)
    assert r.status_code == 429
    assert 0 < int(r.headers["retry-after"]) <= 60


async def test_session_fails_closed_when_redis_down(client_for, session_factory):
    client = await client_for(redis_url="redis://localhost:1/0")
    _, _, key = await connection(session_factory)
    assert (await open_session(client, key)).status_code == 503


# ---------- messages ----------


async def test_send_then_replay_returns_same_message(client, session_factory):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)
    cmid = str(uuid4())

    first = await send(client, token, "  xin  chào\x00 ", cmid)
    replay = await send(client, token, "khác cũng không sao", cmid)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["sequence"] == 1
    assert await count(session_factory, "messages") == 1

    msgs = (await client.get("/public/webchat/v1/messages", headers=bearer(token))).json()
    assert msgs["conversation_id"] == first.json()["conversation_id"]
    [m] = msgs["messages"]
    assert m["text"] == "xin  chào"
    assert m["client_message_id"] == cmid
    assert set(m) == {
        "id",
        "sequence",
        "direction",
        "sender_type",
        "text",
        "occurred_at",
        "client_message_id",
    }


async def test_visitors_cannot_see_each_other(client, session_factory):
    _, _, key = await connection(session_factory)
    a, b = await token_for(client, key), await token_for(client, key)
    await send(client, a, "của A")

    msgs = (await client.get("/public/webchat/v1/messages", headers=bearer(b))).json()
    assert msgs == {"conversation_id": None, "messages": [], "has_more": False}


async def test_list_paginates_after_sequence(client, session_factory):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)
    for i in range(5):
        await send(client, token, f"t{i}")

    page = (
        await client.get(
            "/public/webchat/v1/messages",
            params={"after_sequence": 1, "limit": 2},
            headers=bearer(token),
        )
    ).json()
    assert [m["sequence"] for m in page["messages"]] == [2, 3]
    assert page["has_more"] is True


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Basic abc"},
    ],
)
async def test_messages_require_valid_token(client, headers):
    r = await client.post(
        "/public/webchat/v1/messages",
        json={"client_message_id": str(uuid4()), "text": "x"},
        headers=headers,
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize("change", ["connection_disabled", "tenant_suspended"])
async def test_token_rejected_after_connection_or_tenant_disabled(client, session_factory, change):
    ctx, cid, key = await connection(session_factory)
    token = await token_for(client, key)
    async with session_factory() as s:
        if change == "connection_disabled":
            await s.execute(
                text("UPDATE channel_connections SET status='disabled' WHERE id=:id"), {"id": cid}
            )
        else:
            await s.execute(
                text("UPDATE tenants SET status='suspended' WHERE id=:id"), {"id": ctx.tenant_id}
            )
        await s.commit()
    assert (await send(client, token)).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"client_message_id": "not-uuid", "text": "x"},
        {"client_message_id": "00000000-0000-1000-8000-000000000000", "text": "x"},  # v1
        {"client_message_id": str(uuid4()), "text": "   \x00  "},
        {"client_message_id": str(uuid4()), "text": "x" * 4001},
        {"client_message_id": str(uuid4()), "text": "x", "tenant_id": str(uuid4())},
        {"client_message_id": str(uuid4())},
    ],
)
async def test_invalid_message_body(client, session_factory, body):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)
    r = await client.post("/public/webchat/v1/messages", json=body, headers=bearer(token))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert await count(session_factory, "messages") == 0


async def test_body_too_large_and_wrong_content_type(client, session_factory):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)
    big = json.dumps({"client_message_id": str(uuid4()), "text": "x" * 17000})
    r = await client.post(
        "/public/webchat/v1/messages",
        content=big,
        headers=bearer(token) | {"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    r = await client.post(
        "/public/webchat/v1/messages",
        content="client_message_id=x",
        headers=bearer(token) | {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 415


async def test_messages_rate_limited_per_visitor(client, session_factory):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)
    codes = [(await send(client, token)).status_code for _ in range(21)]
    assert codes[:20] == [201] * 20
    assert codes[20] == 429


async def test_messages_still_accepted_when_redis_down(client_for, session_factory):
    _, _, key = await connection(session_factory)
    token = await token_for(await client_for(), key)
    down = await client_for(redis_url="redis://localhost:1/0")
    assert (await send(down, token)).status_code == 201
    codes = [(await send(down, token)).status_code for _ in range(20)]
    assert codes[-1] == 429  # fallback bộ nhớ cục bộ vẫn chặn flood


async def test_transient_db_error_maps_to_503(client, session_factory, monkeypatch):
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)

    async def boom(self, event):
        raise TransientDatabaseError("x")

    monkeypatch.setattr(
        "app.modules.conversation.application.receive_inbound_message.ReceiveInboundMessage.__call__",
        boom,
    )
    r = await send(client, token)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"
