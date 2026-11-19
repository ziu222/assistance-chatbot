"""API nhân viên: đăng nhập, session cookie, CSRF, inbox REST, trả lời, đã đọc, cô lập tenant."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.entrypoints.cli import seed
from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.identity.infrastructure.passwords import hash_password
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.shared.domain.tenant import TenantContext

from .conftest import TEST_SETTINGS
from .factories import make_connection, make_tenant

STAFF_ORIGIN = TEST_SETTINGS["staff_origin"]
CSRF = {"X-Requested-With": "fetch", "Origin": STAFF_ORIGIN}
PASSWORD = "mat-khau-du-dai-123"


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


@pytest.fixture
async def client(new_client):
    return await new_client()


async def staff(session_factory, *, role="agent", ctx=None, email=None):
    async with session_factory() as s:
        ctx = ctx or await make_tenant(s)
        uid = uuid4()
        email = email or f"nv-{uid.hex[:6]}@shop.vn"
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name) "
                "VALUES (:id, :e, :h, 'Nhân viên')"
            ),
            {"id": uid, "e": email, "h": hash_password(PASSWORD)},
        )
        await s.execute(
            text("INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (:t, :u, :r)"),
            {"t": ctx.tenant_id, "u": uid, "r": role},
        )
        conn_id = await make_connection(s, ctx)
        await s.commit()
    return ctx, uid, email, conn_id


async def login(client, email, password=PASSWORD, headers=CSRF):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=headers
    )


async def logged_in(new_client, session_factory, **kw):
    client = await new_client()
    ctx, uid, email, conn_id = await staff(session_factory, **kw)
    r = await login(client, email)
    assert r.status_code == 200, r.text
    return client, ctx, uid, conn_id


async def inbound(session_factory, ctx, conn_id, text_="xin chào", visitor="v1"):
    uow = lambda: SqlConversationUnitOfWork(session_factory)  # noqa: E731
    return await ReceiveInboundMessage(uow)(
        NormalizedChannelEvent(
            tenant_id=ctx.tenant_id,
            channel_connection_id=conn_id,
            provider="webchat",
            external_message_id=str(uuid4()),
            external_customer_id=visitor,
            direction="inbound",
            message_type="text",
            text=text_,
            occurred_at=datetime.now(UTC),
            trace_id="t",
        )
    )


async def reply(client, conversation_id, text_="chào anh", cmid=None):
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"client_message_id": cmid or str(uuid4()), "text": text_},
        headers=CSRF,
    )


async def scalar(session_factory, sql, **params):
    async with session_factory() as s:
        return (await s.execute(text(sql), params)).scalar_one()


# ---------- auth ----------


async def test_login_sets_hardened_cookie_and_me_works(client, session_factory):
    ctx, uid, email, _ = await staff(session_factory)
    r = await login(client, email.upper())  # email không phân biệt hoa thường (citext)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-sid=")
    for attr in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=43200"):
        assert attr.lower() in cookie.lower()

    me = (await client.get("/api/v1/auth/me")).json()
    assert me["user"]["id"] == str(uid)
    assert me["tenant"]["id"] == str(ctx.tenant_id)
    assert me["role"] == "agent"
    assert "password_hash" not in str(me)


async def test_session_token_stored_hashed(client, session_factory):
    _, _, email, _ = await staff(session_factory)
    await login(client, email)
    raw = client.cookies["__Host-sid"]
    stored = await scalar(session_factory, "SELECT token_hash FROM staff_sessions")
    assert raw.encode() not in bytes(stored)
    assert len(bytes(stored)) == 32  # sha256


async def test_wrong_password_and_unknown_email_look_the_same(client, session_factory):
    _, _, email, _ = await staff(session_factory)
    a = await login(client, email, password="sai-mat-khau")
    b = await login(client, "khong-ton-tai@shop.vn")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"] == b.json()["error"]
    assert await scalar(session_factory, "SELECT count(*) FROM staff_sessions") == 0


async def test_logout_deletes_session(client, session_factory):
    _, _, email, _ = await staff(session_factory)
    await login(client, email)
    raw = client.cookies["__Host-sid"]
    assert (await client.post("/api/v1/auth/logout", headers=CSRF)).status_code == 204
    assert await scalar(session_factory, "SELECT count(*) FROM staff_sessions") == 0
    client.cookies.set("__Host-sid", raw)  # cookie cũ bị đánh cắp cũng vô dụng
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE staff_sessions SET expires_at = now() - interval '1 second'",
        "UPDATE tenants SET status = 'suspended'",
        "UPDATE tenant_memberships SET status = 'disabled'",
        "UPDATE users SET status = 'disabled'",
    ],
)
async def test_session_rejected_when_expired_or_revoked(client, session_factory, change):
    _, _, email, _ = await staff(session_factory)
    await login(client, email)
    async with session_factory() as s:
        await s.execute(text(change))
        await s.commit()
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


async def test_login_rejected_for_suspended_tenant(client, session_factory):
    ctx, _, email, _ = await staff(session_factory)
    async with session_factory() as s:
        await s.execute(
            text("UPDATE tenants SET status='suspended' WHERE id=:t"), {"t": ctx.tenant_id}
        )
        await s.commit()
    assert (await login(client, email)).status_code == 401


async def test_login_rate_limited_per_email(client, session_factory):
    _, _, email, _ = await staff(session_factory)
    codes = [(await login(client, email, password="sai")).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429
    assert (await login(client, email)).status_code == 429  # kể cả mật khẩu đúng


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": STAFF_ORIGIN},  # thiếu X-Requested-With
        {"X-Requested-With": "fetch"},  # thiếu Origin
        {"X-Requested-With": "fetch", "Origin": "https://evil.vn"},
    ],
)
async def test_csrf_guard_on_unsafe_methods(new_client, session_factory, headers):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id)
    r = await client.post(f"/api/v1/conversations/{res.conversation_id}/read", headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_rejected"
    r = await login(client, "x@shop.vn", headers=headers)  # login cũng được bảo vệ
    assert r.status_code == 403


async def test_requires_session(client):
    for path in ("/api/v1/auth/me", "/api/v1/conversations", f"/api/v1/conversations/{uuid4()}"):
        assert (await client.get(path)).status_code == 401


# ---------- inbox ----------


async def test_list_conversations_keyset(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    ids = []
    for i in range(5):
        ids.append(
            (await inbound(session_factory, ctx, conn_id, f"t{i}", visitor=f"v{i}")).conversation_id
        )

    page1 = (await client.get("/api/v1/conversations", params={"limit": 2})).json()
    assert [c["id"] for c in page1["items"]] == [str(ids[4]), str(ids[3])]
    page2 = (
        await client.get(
            "/api/v1/conversations", params={"limit": 2, "cursor": page1["next_cursor"]}
        )
    ).json()
    page3 = (
        await client.get(
            "/api/v1/conversations", params={"limit": 2, "cursor": page2["next_cursor"]}
        )
    ).json()
    assert [c["id"] for c in page2["items"]] == [str(ids[2]), str(ids[1])]
    assert [c["id"] for c in page3["items"]] == [str(ids[0])]
    assert page3["next_cursor"] is None

    first = page1["items"][0]
    assert first["last_message_preview"] == "t4"
    assert first["unread_count"] == 1
    assert first["customer"]["display_name"].startswith("Khách #")


async def test_list_filters_status_and_rejects_bad_cursor(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    await inbound(session_factory, ctx, conn_id)
    closed = await client.get("/api/v1/conversations", params={"status": "closed"})
    assert closed.json()["items"] == []
    assert (await client.get("/api/v1/conversations", params={"cursor": "rác"})).status_code == 422
    assert (await client.get("/api/v1/conversations", params={"status": "x"})).status_code == 422


async def test_detail_and_timeline(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id, "<img src=x onerror=alert(1)>")
    await inbound(session_factory, ctx, conn_id, "tin 2")

    detail = (await client.get(f"/api/v1/conversations/{res.conversation_id}")).json()
    assert detail["status"] == "open"
    assert detail["connection"] == {"id": str(conn_id), "provider": "webchat"}
    assert detail["customer"]["id"]

    msgs = (
        await client.get(
            f"/api/v1/conversations/{res.conversation_id}/messages", params={"after_sequence": 0}
        )
    ).json()
    assert [m["sequence"] for m in msgs["messages"]] == [1, 2]
    assert (
        msgs["messages"][0]["text"] == "<img src=x onerror=alert(1)>"
    )  # lưu nguyên văn, UI render text


async def test_reply_is_idempotent_and_emits_signal_for_visitor(new_client, session_factory):
    client, ctx, uid, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id, visitor="v-77")
    cmid = str(uuid4())

    first = await reply(client, res.conversation_id, "  chào anh  ", cmid)
    again = await reply(client, res.conversation_id, "khác", cmid)
    assert first.status_code == 201
    assert again.status_code == 200 and again.json() == first.json()
    assert first.json()["sequence"] == 2

    async with session_factory() as s:
        msg = (
            await s.execute(
                text(
                    "SELECT direction, sender_type, sender_ref, content, status "
                    "FROM messages WHERE sequence = 2"
                )
            )
        ).one()
        payloads = (
            (
                await s.execute(
                    text(
                        "SELECT payload FROM outbox_events "
                        "WHERE event_type='message.created' ORDER BY id"
                    )
                )
            )
            .scalars()
            .all()
        )
        unread = (await s.execute(text("SELECT unread_count FROM conversations"))).scalar_one()
    assert tuple(msg) == ("outbound", "human", str(uid), "chào anh", "sent")
    assert len(payloads) == 2
    assert payloads[1]["visitor_id"] == "v-77"  # relay đẩy tới kênh widget của khách
    assert payloads[1]["direction"] == "outbound"
    assert "content" not in payloads[1] and "text" not in payloads[1]
    assert unread == 1  # tin của nhân viên không tăng unread


async def test_reply_to_closed_conversation_is_409(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id)
    async with session_factory() as s:
        await s.execute(text("UPDATE conversations SET status='closed', closed_at=now()"))
        await s.commit()
    r = await reply(client, res.conversation_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conversation_closed"


async def test_reply_validates_body(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id)
    for body in (
        {"client_message_id": "x", "text": "a"},
        {"client_message_id": str(uuid4()), "text": " \x00 "},
        {"client_message_id": str(uuid4()), "text": "a", "sender_type": "customer"},
    ):
        r = await client.post(
            f"/api/v1/conversations/{res.conversation_id}/messages", json=body, headers=CSRF
        )
        assert r.status_code == 422


async def test_mark_read(new_client, session_factory):
    client, ctx, _, conn_id = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx, conn_id)
    await inbound(session_factory, ctx, conn_id)

    r = await client.post(f"/api/v1/conversations/{res.conversation_id}/read", headers=CSRF)
    assert r.status_code == 204
    assert await scalar(session_factory, "SELECT unread_count FROM conversations") == 0
    assert (
        await scalar(
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type='conversation.read'",
        )
        == 1
    )
    await client.post(f"/api/v1/conversations/{res.conversation_id}/read", headers=CSRF)
    assert (
        await scalar(  # đã đọc rồi -> không phát event thừa
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type='conversation.read'",
        )
        == 1
    )


# ---------- cô lập tenant ----------


async def test_tenant_b_cannot_touch_tenant_a(new_client, session_factory):
    _, ctx_a, _, conn_a = await logged_in(new_client, session_factory)
    res = await inbound(session_factory, ctx_a, conn_a)
    conv = res.conversation_id
    client_b, *_ = await logged_in(new_client, session_factory, role="owner")

    assert (await client_b.get("/api/v1/conversations")).json()["items"] == []
    assert (await client_b.get(f"/api/v1/conversations/{conv}")).status_code == 404
    assert (await client_b.get(f"/api/v1/conversations/{conv}/messages")).status_code == 404
    assert (await reply(client_b, conv)).status_code == 404
    assert (
        await client_b.post(f"/api/v1/conversations/{conv}/read", headers=CSRF)
    ).status_code == 404

    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM messages"))).scalar_one() == 1
        assert (await s.execute(text("SELECT unread_count FROM conversations"))).scalar_one() == 1


# ---------- đồng thời ----------


async def test_concurrent_inbound_and_staff_replies_keep_sequence_gapless(session_factory):
    ctx, uid, _, conn_id = await staff(session_factory)
    res = await inbound(session_factory, ctx, conn_id, visitor="v-c")
    send = SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))

    async def staff_reply(i):
        return await send(
            TenantContext(ctx.tenant_id),
            res.conversation_id,
            user_id=uid,
            client_message_id=str(uuid4()),
            text=f"nv {i}",
            trace_id="t",
        )

    await asyncio.gather(
        *(staff_reply(i) for i in range(20)),
        *(inbound(session_factory, ctx, conn_id, f"kh {i}", visitor="v-c") for i in range(20)),
    )
    async with session_factory() as s:
        seqs = (
            (await s.execute(text("SELECT sequence FROM messages ORDER BY sequence")))
            .scalars()
            .all()
        )
        last = (await s.execute(text("SELECT last_sequence FROM conversations"))).scalar_one()
    assert seqs == list(range(1, 42))
    assert last == 41


# ---------- seed CLI ----------


async def test_seed_creates_tenant_owner_and_widget_then_login_works(client, session_factory):
    async with session_factory() as s:
        result = await seed(
            s, tenant_name="Shop A", email="owner@shop.vn", origins=["https://shop.vn"]
        )
        await s.commit()
    assert result.public_key.startswith("wpk_") and len(result.public_key) == 28
    assert len(result.password) >= 16
    stored = await scalar(session_factory, "SELECT password_hash FROM users")
    assert stored.startswith("$argon2id$") and result.password not in stored

    assert (await login(client, "owner@shop.vn", password=result.password)).status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["role"] == "owner"
