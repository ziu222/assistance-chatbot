"""Cổng gửi tin AI `outbound.send_ai_reply` (thiết kế 06 A1): kiểm dưới khóa + idempotent."""

from uuid import uuid4

from sqlalchemy import text

from app.modules.outbound.ai_reply import send_ai_reply

from .factories import (
    insert_message,
    make_connection,
    make_conversation,
    make_customer,
    make_tenant,
)


async def _setup_conv(session_factory, *, handling: str = "bot", status: str = "open"):
    async with session_factory() as s, s.begin():
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        cust = await make_customer(s, ctx)
        conv = await make_conversation(s, ctx, cust, conn, status=status)
        await insert_message(s, ctx, conv, conn, sequence=1, content="Shop mở cửa mấy giờ?")
        await s.execute(
            text(
                "UPDATE conversations SET last_sequence = 1, handling = :h "
                "WHERE tenant_id = :t AND id = :c"
            ),
            {"h": handling, "t": ctx.tenant_id, "c": conv},
        )
    return ctx, conn, conv


async def test_send_ai_reply_writes_bot_message_outbox_and_is_idempotent(session_factory):
    ctx, conn, conv = await _setup_conv(session_factory)
    draft_id, run_id = uuid4(), uuid4()

    async with session_factory() as s, s.begin():
        first = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv,
            draft_id=draft_id,
            run_id=run_id,
            based_on_sequence=1,
            content="Dạ shop mở cửa từ 8h đến 21h hàng ngày ạ.",
        )
    assert first.sent is True
    assert first.sequence == 2
    assert first.message_id is not None

    # Chạy lại worker với cùng draft_id ⇒ idempotent, không gửi lần hai
    async with session_factory() as s, s.begin():
        retry = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv,
            draft_id=draft_id,
            run_id=run_id,
            based_on_sequence=1,
            content="Dạ shop mở cửa từ 8h đến 21h hàng ngày ạ.",
        )
    assert retry.sent is False
    assert retry.duplicate is True
    assert retry.message_id == first.message_id

    async with session_factory() as s:
        msgs = (
            await s.execute(
                text(
                    "SELECT sender_type, draft_id, external_message_id, sequence "
                    "FROM messages WHERE tenant_id = :t AND conversation_id = :c "
                    "ORDER BY sequence"
                ),
                {"t": ctx.tenant_id, "c": conv},
            )
        ).all()
        outbox = (
            await s.execute(
                text(
                    "SELECT event_type, payload FROM outbox_events "
                    "WHERE tenant_id = :t AND aggregate_id = :c"
                ),
                {"t": ctx.tenant_id, "c": conv},
            )
        ).all()
    assert len(msgs) == 2
    assert msgs[1].sender_type == "bot"
    assert msgs[1].draft_id == draft_id
    assert msgs[1].external_message_id == f"ai:{draft_id}"
    assert len(outbox) == 1
    assert outbox[0].payload["sender_type"] == "bot"
    assert outbox[0].payload["draft_id"] == str(draft_id)


async def test_recheck_blocks_on_newer_customer_message(session_factory):
    ctx, conn, conv = await _setup_conv(session_factory)
    async with session_factory() as s, s.begin():
        await insert_message(s, ctx, conv, conn, sequence=2, content="À thôi cho mình hỏi thêm")
        await s.execute(
            text("UPDATE conversations SET last_sequence = 2 WHERE tenant_id = :t AND id = :c"),
            {"t": ctx.tenant_id, "c": conv},
        )
        res = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop mở 8h-21h ạ.",
        )
    assert res.sent is False
    assert res.blocked_reason == "stale"


async def test_recheck_blocks_when_human_already_replied(session_factory):
    ctx, conn, conv = await _setup_conv(session_factory)
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                "external_message_id, direction, sender_type, content, status, sequence, "
                "occurred_at) VALUES (:id, :t, :conn, :c, :ext, 'outbound', 'human', "
                "'Em chào chị ạ', 'sent', 2, now())"
            ),
            {"id": uuid4(), "t": ctx.tenant_id, "conn": conn, "c": conv, "ext": str(uuid4())},
        )
        await s.execute(
            text("UPDATE conversations SET last_sequence = 2 WHERE tenant_id = :t AND id = :c"),
            {"t": ctx.tenant_id, "c": conv},
        )
        res = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop mở 8h-21h ạ.",
        )
    assert res.sent is False
    assert res.blocked_reason == "stale"


async def test_recheck_blocks_on_handling_closed_streak_channel_and_callback(session_factory):
    # 1. handling != bot
    ctx1, _, conv1 = await _setup_conv(session_factory, handling="human")
    async with session_factory() as s, s.begin():
        r1 = await send_ai_reply(
            s,
            tenant_id=ctx1.tenant_id,
            conversation_id=conv1,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="ok",
        )
    assert r1.blocked_reason == "handling"

    # 2. conversation closed
    ctx2, _, conv2 = await _setup_conv(session_factory, status="closed")
    async with session_factory() as s, s.begin():
        r2 = await send_ai_reply(
            s,
            tenant_id=ctx2.tenant_id,
            conversation_id=conv2,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="ok",
        )
    assert r2.blocked_reason == "handling"

    # 3. bot_streak >= 3
    ctx3, _, conv3 = await _setup_conv(session_factory)
    for _ in range(3):
        async with session_factory() as s, s.begin():
            ok = await send_ai_reply(
                s,
                tenant_id=ctx3.tenant_id,
                conversation_id=conv3,
                draft_id=uuid4(),
                run_id=uuid4(),
                based_on_sequence=1,
                content="Tin bot",
            )
            assert ok.sent is True
    async with session_factory() as s, s.begin():
        r3 = await send_ai_reply(
            s,
            tenant_id=ctx3.tenant_id,
            conversation_id=conv3,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Tin bot thứ 4",
        )
    assert r3.blocked_reason == "bot_streak"

    # 4. channel not allowed
    ctx4, _, conv4 = await _setup_conv(session_factory)
    async with session_factory() as s, s.begin():
        r4 = await send_ai_reply(
            s,
            tenant_id=ctx4.tenant_id,
            conversation_id=conv4,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="ok",
            channel_allowed=False,
        )
    assert r4.blocked_reason == "channel"

    # 5. recheck callback blocks under lock (e.g. kill_switch or intent demoted)
    async def _demoted(_s):
        return "intent_not_enabled"

    async with session_factory() as s, s.begin():
        r5 = await send_ai_reply(
            s,
            tenant_id=ctx4.tenant_id,
            conversation_id=conv4,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="ok",
            recheck=_demoted,
        )
    assert r5.blocked_reason == "intent_not_enabled"
