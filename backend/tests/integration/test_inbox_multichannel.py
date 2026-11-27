"""Kiểm thử inbox đa kênh (O5.4): lọc theo provider, trả capabilities, kiểm cửa sổ & độ dài."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from .factories import make_customer
from .test_staff_api import CSRF, inbound, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def _seed_messenger_conversation(
    session_factory,
    ctx,
    *,
    last_customer_at: datetime,
):
    async with session_factory() as s, s.begin():
        conn_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO channel_connections "
                "(id, tenant_id, provider, external_account_id, status, config) "
                "VALUES (:id, :t, 'messenger', :ext, 'active', '{}'::jsonb)"
            ),
            {"id": conn_id, "t": ctx.tenant_id, "ext": f"page_{conn_id.hex[:8]}"},
        )
        cust_id = await make_customer(s, ctx)
        await s.execute(
            text(
                "INSERT INTO channel_identities "
                "(id, tenant_id, customer_id, channel_connection_id, provider, scoped_external_id) "
                "VALUES (:id, :t, :cu, :conn, 'messenger', :psid)"
            ),
            {
                "id": uuid4(),
                "t": ctx.tenant_id,
                "cu": cust_id,
                "conn": conn_id,
                "psid": f"psid_{cust_id.hex[:6]}",
            },
        )
        conv_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, customer_id, channel_connection_id, status, "
                " last_sequence, last_message_at, last_message_preview, unread_count) "
                "VALUES (:id, :t, :cu, :conn, 'open', 1, :at, 'Chào shop', 1)"
            ),
            {
                "id": conv_id,
                "t": ctx.tenant_id,
                "cu": cust_id,
                "conn": conn_id,
                "at": last_customer_at,
            },
        )
        await s.execute(
            text(
                "INSERT INTO messages "
                "(id, tenant_id, channel_connection_id, conversation_id, external_message_id, "
                " direction, sender_type, content, status, sequence, occurred_at) "
                "VALUES (:id, :t, :conn, :c, :ext, 'inbound', 'customer', 'Chào shop', 'received', "
                " 1, :at)"
            ),
            {
                "id": uuid4(),
                "t": ctx.tenant_id,
                "conn": conn_id,
                "c": conv_id,
                "ext": f"mid_{uuid4()}",
                "at": last_customer_at,
            },
        )
    return conn_id, conv_id


async def test_list_conversations_filters_provider_and_includes_capabilities(
    new_client, session_factory
):
    client, ctx, _, web_conn = await logged_in(new_client, session_factory)
    web_res = await inbound(session_factory, ctx, web_conn, "Tin webchat", visitor="v1")
    now = datetime.now(UTC)
    _, msg_conv = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=now - timedelta(minutes=10)
    )

    all_res = await client.get("/api/v1/conversations")
    assert all_res.status_code == 200
    assert len(all_res.json()["items"]) == 2

    msg_only = await client.get("/api/v1/conversations", params={"provider": "messenger"})
    assert msg_only.status_code == 200
    items = msg_only.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(msg_conv)
    assert items[0]["connection"]["provider"] == "messenger"
    assert items[0]["capabilities"] == {
        "reply_window_seconds": 86400,
        "supports_links": True,
        "supports_buttons": True,
        "max_text_length": 2000,
    }

    web_only = await client.get("/api/v1/conversations", params={"provider": "webchat"})
    assert web_only.status_code == 200
    web_items = web_only.json()["items"]
    assert len(web_items) == 1
    assert web_items[0]["id"] == str(web_res.conversation_id)
    assert web_items[0]["capabilities"] == {
        "reply_window_seconds": None,
        "supports_links": True,
        "supports_buttons": False,
        "max_text_length": 4000,
    }

    bad = await client.get("/api/v1/conversations", params={"provider": "shopee"})
    assert bad.status_code == 422


async def test_staff_reply_messenger_window_and_length_enforcement(new_client, session_factory):
    client, ctx, _, _ = await logged_in(new_client, session_factory)
    now = datetime.now(UTC)
    _, conv_ok = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=now - timedelta(hours=2)
    )
    _, conv_expired = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=now - timedelta(hours=25)
    )

    # Trong 24h và <= 2000 ký tự ⇒ 201 thành công
    r_ok = await client.post(
        f"/api/v1/conversations/{conv_ok}/messages",
        json={"client_message_id": str(uuid4()), "text": "Dạ shop còn hàng ạ."},
        headers=CSRF,
    )
    assert r_ok.status_code == 201, r_ok.text

    # Quá 2000 ký tự trên Messenger ⇒ 422 message_too_long, không ghi thêm tin
    r_long = await client.post(
        f"/api/v1/conversations/{conv_ok}/messages",
        json={"client_message_id": str(uuid4()), "text": "a" * 2001},
        headers=CSRF,
    )
    assert r_long.status_code == 422
    assert r_long.json()["error"]["code"] == "message_too_long"

    # Quá 24h trên Messenger ⇒ 409 messenger_window_expired, không ghi thêm tin
    r_exp = await client.post(
        f"/api/v1/conversations/{conv_expired}/messages",
        json={"client_message_id": str(uuid4()), "text": "Xin chào ạ"},
        headers=CSRF,
    )
    assert r_exp.status_code == 409
    assert r_exp.json()["error"]["code"] == "messenger_window_expired"

    async with session_factory() as s:
        ok_count = (
            await s.execute(
                text("SELECT count(*) FROM messages WHERE tenant_id = :t AND conversation_id = :c"),
                {"t": ctx.tenant_id, "c": conv_ok},
            )
        ).scalar_one()
        exp_count = (
            await s.execute(
                text("SELECT count(*) FROM messages WHERE tenant_id = :t AND conversation_id = :c"),
                {"t": ctx.tenant_id, "c": conv_expired},
            )
        ).scalar_one()
    assert ok_count == 2
    assert exp_count == 1


async def test_staff_reply_idempotent_after_window_expires_and_closed_wins(
    new_client, session_factory
):
    client, ctx, _, _ = await logged_in(new_client, session_factory)
    now = datetime.now(UTC)
    _, conv_id = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=now - timedelta(hours=2)
    )
    cmid = str(uuid4())

    # 1. Gửi thành công khi vẫn còn trong cửa sổ 24h.
    first = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"client_message_id": cmid, "text": "Dạ em gửi thông tin ạ."},
        headers=CSRF,
    )
    assert first.status_code == 201, first.text

    # Kéo tin khách về quá 24h; retry cùng client_message_id vẫn trả 200 OK (idempotent).
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE messages SET occurred_at = :old "
                "WHERE tenant_id = :t AND conversation_id = :c AND sender_type = 'customer'"
            ),
            {"old": now - timedelta(hours=25), "t": ctx.tenant_id, "c": conv_id},
        )
    retry = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"client_message_id": cmid, "text": "Nội dung khác nếu retry"},
        headers=CSRF,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json()

    # 2. Hội thoại vừa closed vừa quá 24h ⇒ trả 409 conversation_closed (ưu tiên trước cửa sổ).
    _, conv_closed_expired = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=now - timedelta(hours=25)
    )
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE conversations SET status = 'closed', closed_at = now() "
                "WHERE tenant_id = :t AND id = :c"
            ),
            {"t": ctx.tenant_id, "c": conv_closed_expired},
        )
    r_closed = await client.post(
        f"/api/v1/conversations/{conv_closed_expired}/messages",
        json={"client_message_id": str(uuid4()), "text": "Xin chào"},
        headers=CSRF,
    )
    assert r_closed.status_code == 409
    assert r_closed.json()["error"]["code"] == "conversation_closed"


async def test_conversation_returns_reply_window_expires_at_and_message_delivery_status(
    new_client, session_factory
):
    client, ctx, _, web_conn = await logged_in(new_client, session_factory)
    web_res = await inbound(session_factory, ctx, web_conn, "Tin webchat", visitor="v-win")
    last_customer_at = (datetime.now(UTC) - timedelta(hours=3)).replace(microsecond=0)
    _, msg_conv = await _seed_messenger_conversation(
        session_factory, ctx, last_customer_at=last_customer_at
    )

    web_detail = (await client.get(f"/api/v1/conversations/{web_res.conversation_id}")).json()
    assert web_detail["reply_window_expires_at"] is None

    msg_detail = (await client.get(f"/api/v1/conversations/{msg_conv}")).json()
    assert msg_detail["reply_window_expires_at"] is not None
    assert datetime.fromisoformat(
        msg_detail["reply_window_expires_at"]
    ) == last_customer_at + timedelta(hours=24)

    # Nhân viên trả lời Webchat ⇒ delivery_status luôn là None
    web_reply = await client.post(
        f"/api/v1/conversations/{web_res.conversation_id}/messages",
        json={"client_message_id": str(uuid4()), "text": "Chào khách web"},
        headers=CSRF,
    )
    assert web_reply.status_code == 201
    web_msgs = (
        await client.get(f"/api/v1/conversations/{web_res.conversation_id}/messages")
    ).json()["messages"]
    assert [m["delivery_status"] for m in web_msgs] == [None, None]

    # Nhân viên trả lời Messenger ⇒ delivery_status = "pending"; cửa sổ vẫn neo theo tin khách cuối
    msg_reply = await client.post(
        f"/api/v1/conversations/{msg_conv}/messages",
        json={"client_message_id": str(uuid4()), "text": "Chào khách FB"},
        headers=CSRF,
    )
    assert msg_reply.status_code == 201
    reply_id = msg_reply.json()["message_id"]

    msg_detail_after = (await client.get(f"/api/v1/conversations/{msg_conv}")).json()
    assert datetime.fromisoformat(
        msg_detail_after["reply_window_expires_at"]
    ) == last_customer_at + timedelta(hours=24)

    msgs_pending = (await client.get(f"/api/v1/conversations/{msg_conv}/messages")).json()[
        "messages"
    ]
    assert [m["delivery_status"] for m in msgs_pending] == [None, "pending"]

    # Khi outbound_deliveries chuyển delivered/failed ⇒ API phản ánh ngay và không lộ last_error
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE outbound_deliveries SET status = 'delivered', delivered_at = now() "
                "WHERE tenant_id = :t AND message_id = :m"
            ),
            {"t": ctx.tenant_id, "m": reply_id},
        )
    msgs_delivered = (await client.get(f"/api/v1/conversations/{msg_conv}/messages")).json()[
        "messages"
    ]
    assert msgs_delivered[1]["delivery_status"] == "delivered"

    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE outbound_deliveries SET status = 'failed', "
                "last_error = 'secret_token_leak' "
                "WHERE tenant_id = :t AND message_id = :m"
            ),
            {"t": ctx.tenant_id, "m": reply_id},
        )
    msgs_failed = (await client.get(f"/api/v1/conversations/{msg_conv}/messages")).json()[
        "messages"
    ]
    assert msgs_failed[1]["delivery_status"] == "failed"
    assert "last_error" not in msgs_failed[1]
