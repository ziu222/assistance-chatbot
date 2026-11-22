"""Bước 2.9: tin tự động do chủ shop viết được gửi qua cổng duy nhất của module `outbound`."""

import dataclasses
from uuid import uuid4

from sqlalchemy import text

from app.modules.conversation_policy.infrastructure.repository import publish, set_auto_send

from .test_agent_runtime import make_runner, make_world, one_run, request_for
from .test_conversation_policy import SAT_NIGHT, config, decisions, handling, stage
from .test_staff_api import CSRF, logged_in


async def setup(session_factory, *, auto_send=True, after_next="bot"):
    w = await make_world(session_factory)
    async with session_factory() as s, s.begin():
        await publish(s, w.tenant_id, config(after_next=after_next), uuid4())
        await set_auto_send(s, w.tenant_id, auto_send)
    return w


async def system_messages(session_factory) -> list:
    async with session_factory() as s:
        rows = await s.execute(
            text(
                "SELECT sequence, content, sender_ref, direction FROM messages "
                "WHERE sender_type = 'system' ORDER BY sequence"
            )
        )
        return rows.mappings().all()


async def test_switch_off_records_but_does_not_send(session_factory):
    w = await setup(session_factory, auto_send=False)
    runner, _ = make_runner(session_factory, [])
    await stage(session_factory, runner, SAT_NIGHT).handle(
        await request_for(session_factory, w, ("alo shop",))
    )
    [d] = await decisions(session_factory)
    assert (d["sent"], d["send_block"]) == (False, "auto_send_off")
    assert await system_messages(session_factory) == []


async def test_after_hours_message_is_sent_and_agent_still_drafts(session_factory):
    w = await setup(session_factory)
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("alo shop",))
    await stage(session_factory, runner, SAT_NIGHT).handle(req)

    [m] = await system_messages(session_factory)
    assert m["content"] == "Dạ shop mở lại lúc 08:00 thứ Hai (23/11) ạ."
    assert (m["sequence"], m["direction"]) == (req.to_sequence + 1, "outbound")
    assert m["sender_ref"].startswith("policy:after_hours:")
    [d] = await decisions(session_factory)
    assert (d["sent"], d["send_block"]) == (True, None)

    async with session_factory() as s:
        conv = (
            await s.execute(text("SELECT last_sequence, unread_count, handling FROM conversations"))
        ).one()
        payload = (
            await s.execute(
                text(
                    "SELECT payload FROM outbox_events WHERE event_type = 'message.created' "
                    "AND payload->>'sender_type' = 'system'"
                )
            )
        ).scalar_one()
    assert conv.last_sequence == req.to_sequence + 1 and conv.handling == "bot"
    assert payload["visitor_id"]  # widget nhận tín hiệu
    # Tin tự động không làm run bị superseded: agent vẫn soạn nháp (đường rule câu chào).
    assert (await one_run(session_factory))["status"] == "completed"


async def test_retry_sends_once(session_factory):
    w = await setup(session_factory)
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("alo shop",))
    st = stage(session_factory, runner, SAT_NIGHT)
    await st.handle(req)
    await st.handle(req)
    assert len(await system_messages(session_factory)) == 1


async def test_once_per_closed_period(session_factory):
    w = await setup(session_factory)
    runner, _ = make_runner(session_factory, [])
    st = stage(session_factory, runner, SAT_NIGHT)
    first = await request_for(session_factory, w, ("alo shop",))
    await st.handle(first)
    second = await request_for(session_factory, w, ("cảm ơn shop",))
    second = dataclasses.replace(second, from_sequence=first.to_sequence + 2)
    await st.handle(second)
    assert len(await system_messages(session_factory)) == 1


async def test_send_then_wait_for_staff(session_factory):
    w = await setup(session_factory, after_next="waiting_human")
    runner, llm = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("áo khoác gió giá bao nhiêu",))
    await stage(session_factory, runner, SAT_NIGHT).handle(req)
    assert len(await system_messages(session_factory)) == 1
    assert await handling(session_factory, req.conversation_id) == "waiting_human"
    assert (await one_run(session_factory))["skip_reason"] == "handling_waiting"
    assert llm.requests == []


async def test_settings_api(app_client, session_factory):
    client, *_ = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="owner"
    )
    url = "/api/v1/policy/settings"
    early = await client.patch(url, json={"auto_send": True}, headers=CSRF)
    assert early.status_code == 409  # chưa lưu tin tự động nào
    saved = await client.put("/api/v1/policy", json=config().model_dump(mode="json"), headers=CSRF)
    assert saved.status_code == 200
    assert (await client.get("/api/v1/policy")).json()["auto_send"] is False  # mặc định tắt
    assert (await client.patch(url, json={"auto_send": True}, headers=CSRF)).status_code == 200
    assert (await client.get("/api/v1/policy")).json()["auto_send"] is True
    # Lưu phiên bản mới không tắt công tắc.
    await client.put("/api/v1/policy", json=config().model_dump(mode="json"), headers=CSRF)
    assert (await client.get("/api/v1/policy")).json()["auto_send"] is True
