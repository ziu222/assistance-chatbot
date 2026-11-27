"""Bước 2.8: ticket "Nhờ nhân viên" — nút widget, cụm từ, chủ đề shop, thao tác của nhân viên."""

import asyncio
from uuid import uuid4

from sqlalchemy import text

from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.conversation_policy.domain.policy import HandoffAckConfig, PolicyConfig
from app.modules.conversation_policy.infrastructure.handoffs import open_handoff
from app.modules.conversation_policy.infrastructure.repository import publish, set_auto_send

from .test_agent_runtime import make_runner, make_world, one_run, request_for
from .test_conversation_policy import SAT_MORNING, handling, stage
from .test_public_webchat import bearer, connection, send, token_for
from .test_staff_api import CSRF, inbound, logged_in


async def tickets(session_factory) -> list:
    async with session_factory() as s:
        rows = await s.execute(text("SELECT * FROM handoff_requests ORDER BY opened_at"))
        return rows.mappings().all()


async def signals(session_factory) -> list[str]:
    async with session_factory() as s:
        return list(
            (await s.execute(text("SELECT kind FROM learning_signals ORDER BY id"))).scalars()
        )


async def test_widget_button_opens_one_ticket(app_client, session_factory):
    from .test_public_webchat import WIDGET_ORIGIN

    client = await app_client(WIDGET_ORIGIN)
    _, _, key = await connection(session_factory)
    token = await token_for(client, key)

    early = await client.post("/public/webchat/v1/handoff", json={}, headers=bearer(token))
    assert early.status_code == 409  # chưa nhắn tin: chưa có hội thoại

    assert (await send(client, token)).status_code == 201
    results = [
        (await client.post("/public/webchat/v1/handoff", json={}, headers=bearer(token))).json()
        for _ in range(5)
    ]
    assert [r["created"] for r in results] == [True, False, False, False, False]
    [t] = await tickets(session_factory)
    assert (t["source"], t["status"]) == ("customer_button", "open")
    assert await handling(session_factory, t["conversation_id"]) == "waiting_human"
    assert await signals(session_factory) == ["handoff_opened"]
    async with session_factory() as s:
        events = (
            await s.execute(text("SELECT event_type, payload FROM outbox_events ORDER BY id"))
        ).all()
    opened = [p for e, p in events if e == "handoff.opened"]
    assert len(opened) == 1 and "visitor_id" not in opened[0]  # chỉ phát tới nhân viên


async def test_parallel_requests_still_one_ticket(session_factory):
    w = await make_world(session_factory)
    req = await request_for(session_factory, w, ("alo",))

    async def press():
        async with session_factory() as s, s.begin():
            return await open_handoff(s, w.tenant_id, req.conversation_id, source="customer_button")

    results = await asyncio.gather(*(press() for _ in range(5)), return_exceptions=True)
    assert len(await tickets(session_factory)) == 1
    created = [r for r in results if not isinstance(r, BaseException) and r[1]]
    assert len(created) == 1


async def test_phrase_opens_ticket_without_policy_config(session_factory):
    w = await make_world(session_factory)  # chưa lưu cấu hình tin tự động
    runner, llm = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("cho em gặp nhân viên với",))
    await stage(session_factory, runner, SAT_MORNING).handle(req)

    [t] = await tickets(session_factory)
    assert (t["source"], t["reason"]) == ("phrase", "gap nhan vien")
    run = await one_run(session_factory)
    assert (run["status"], run["skip_reason"]) == ("skipped", "handling_waiting")
    assert llm.requests == []


async def test_owner_topic_opens_ticket(session_factory):
    w = await make_world(session_factory)
    async with session_factory() as s, s.begin():
        await publish(s, w.tenant_id, PolicyConfig(handoff_topics=["Đổi size"]), uuid4())
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("áo hơi chật, doi size duoc ko shop",))
    await stage(session_factory, runner, SAT_MORNING).handle(req)
    [t] = await tickets(session_factory)
    assert (t["source"], t["reason"]) == ("topic", "Đổi size")
    async with session_factory() as s:
        d = (await s.execute(text("SELECT * FROM policy_decisions"))).mappings().one()
    assert (d["handling_before"], d["run_agent"]) == ("waiting_human", False)


async def test_staff_reply_claims_ticket(session_factory):
    w = await make_world(session_factory)
    req = await request_for(session_factory, w, ("alo",))
    async with session_factory() as s, s.begin():
        await open_handoff(s, w.tenant_id, req.conversation_id, source="customer_button")
    staff = uuid4()
    await SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))(
        w.ctx,
        req.conversation_id,
        user_id=staff,
        client_message_id=str(uuid4()),
        text="Dạ em đây ạ",
        trace_id=None,
    )
    [t] = await tickets(session_factory)
    assert (t["status"], t["claimed_by"]) == ("claimed", staff)
    assert await handling(session_factory, req.conversation_id) == "human"


# ---------- API nhân viên ----------


async def test_queue_and_actions(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="agent"
    )
    first = await inbound(session_factory, ctx, conn, visitor="v1")
    second = await inbound(session_factory, ctx, conn, visitor="v2")
    for res in (first, second):
        r = await client.post(f"/api/v1/conversations/{res.conversation_id}/handoff", headers=CSRF)
        assert r.status_code == 200 and r.json()["created"] is True

    items = (await client.get("/api/v1/handoffs")).json()["items"]
    assert [i["conversation_id"] for i in items] == [
        str(first.conversation_id),
        str(second.conversation_id),
    ]  # cũ nhất trước
    a, b = items

    # Không cần ⇒ đóng, gắn nhãn, bot tư vấn lại.
    r = await client.post(f"/api/v1/handoffs/{a['id']}/dismiss", headers=CSRF)
    assert r.status_code == 200
    assert await handling(session_factory, first.conversation_id) == "bot"
    again = await client.post(f"/api/v1/handoffs/{a['id']}/claim", headers=CSRF)
    assert again.status_code == 409  # đã đóng

    # Nhận ⇒ nhân viên xử lý; xong ⇒ rời hàng đợi.
    act = lambda action: client.post(f"/api/v1/handoffs/{b['id']}/{action}", headers=CSRF)  # noqa: E731
    assert (await act("claim")).status_code == 200
    assert await handling(session_factory, second.conversation_id) == "human"
    assert (await act("resolve")).status_code == 200
    assert (await client.get("/api/v1/handoffs")).json()["items"] == []

    rows = {str(t["id"]): t for t in await tickets(session_factory)}
    assert rows[a["id"]]["not_needed"] is True
    assert await signals(session_factory) == [
        "handoff_opened",
        "handoff_opened",
        "handoff_not_needed",
        "handoff_resolved",
    ]


async def test_other_tenant_cannot_touch_ticket(app_client, session_factory):
    w = await make_world(session_factory)
    req = await request_for(session_factory, w, ("alo",))
    async with session_factory() as s, s.begin():
        hid, _ = await open_handoff(s, w.tenant_id, req.conversation_id, source="customer_button")
    client, *_ = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="owner"
    )
    assert (await client.get("/api/v1/handoffs")).json()["items"] == []
    r = await client.post(f"/api/v1/handoffs/{hid}/claim", headers=CSRF)
    assert r.status_code == 409
    other = await client.post(f"/api/v1/conversations/{req.conversation_id}/handoff", headers=CSRF)
    assert other.status_code == 404


async def test_handoff_ack_sent_once_when_auto_send_enabled(session_factory):
    w = await make_world(session_factory)
    cfg = PolicyConfig(
        handoff_ack=HandoffAckConfig(
            enabled=True, text="Dạ {shop_name} đã chuyển nhân viên hỗ trợ bạn ạ."
        )
    )
    async with session_factory() as s, s.begin():
        await publish(s, w.tenant_id, cfg, uuid4())
        await set_auto_send(s, w.tenant_id, True)
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("cho em gặp nhân viên với",))
    await stage(session_factory, runner, SAT_MORNING).handle(req)
    # Retry cùng cụm tin: không gửi lặp handoff_ack.
    await stage(session_factory, runner, SAT_MORNING).handle(req)

    async with session_factory() as s:
        sys_msgs = (
            await s.execute(
                text(
                    "SELECT content, sender_ref FROM messages "
                    "WHERE conversation_id = :c AND sender_type = 'system' ORDER BY sequence"
                ),
                {"c": req.conversation_id},
            )
        ).all()
    assert len(sys_msgs) == 1
    assert "đã chuyển nhân viên hỗ trợ bạn ạ." in sys_msgs[0][0]
    assert sys_msgs[0][1].startswith("policy:handoff_ack:")


async def test_handoff_ack_blocked_when_auto_send_off(session_factory):
    w = await make_world(session_factory)
    cfg = PolicyConfig(
        handoff_ack=HandoffAckConfig(enabled=True, text="Dạ {shop_name} đã chuyển nhân viên ạ.")
    )
    async with session_factory() as s, s.begin():
        await publish(s, w.tenant_id, cfg, uuid4())
        # auto_send mặc định False
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("cho em gặp nhân viên với",))
    await stage(session_factory, runner, SAT_MORNING).handle(req)

    async with session_factory() as s:
        count = (
            await s.execute(
                text(
                    "SELECT count(*) FROM messages "
                    "WHERE conversation_id = :c AND sender_type = 'system'"
                ),
                {"c": req.conversation_id},
            )
        ).scalar_one()
    assert count == 0


async def test_queue_orders_higher_priority_first(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="agent"
    )
    low = await inbound(session_factory, ctx, conn, visitor="v_low")
    high = await inbound(session_factory, ctx, conn, visitor="v_high")
    async with session_factory() as s, s.begin():
        await open_handoff(s, ctx.tenant_id, low.conversation_id, source="topic", priority=0)
        await open_handoff(s, ctx.tenant_id, high.conversation_id, source="phrase", priority=2)

    items = (await client.get("/api/v1/handoffs")).json()["items"]
    assert [i["conversation_id"] for i in items] == [
        str(high.conversation_id),
        str(low.conversation_id),
    ]
    assert [i["priority"] for i in items] == [2, 0]
