"""Integration tests cho HandoffSummaryWorker và API `/api/v1/handoffs` (bước 2.8)."""

from sqlalchemy import text

from app.modules.agent_runtime.llm.fake import FakeLLM, text_result
from app.modules.agent_runtime.llm.port import ProviderUnavailable
from app.modules.conversation_policy.domain.summary import HandoffSummaryOut
from app.modules.conversation_policy.infrastructure.handoffs import open_handoff
from app.modules.conversation_policy.infrastructure.summary import HandoffSummaryWorker

from .test_agent_runtime import make_world, request_for
from .test_staff_api import inbound, logged_in


async def test_summary_worker_summarizes_and_exposes_in_api(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="agent"
    )
    first = await inbound(session_factory, ctx, conn, visitor="v_sum")
    await inbound(session_factory, ctx, conn, visitor="v_sum")
    async with session_factory() as s, s.begin():
        hid, _ = await open_handoff(
            s, ctx.tenant_id, first.conversation_id, source="customer_button", priority=1
        )

    out = HandoffSummaryOut(
        lines=["Khách muốn đổi áo khoác từ size M sang size L", "Cần nhân viên xác nhận đổi size"]
    )
    llm = FakeLLM([text_result("", parsed=out)])
    worker = HandoffSummaryWorker(session_factory, llm, daily_cap_micro=200_000)
    assert await worker.process_once() is True
    assert await worker.process_once() is False
    assert len(llm.requests) == 1

    [item] = (await client.get("/api/v1/handoffs")).json()["items"]
    assert item["id"] == str(hid)
    assert item["summary_status"] == "done"
    assert "size M sang size L" in item["summary"]

    async with session_factory() as s:
        events = (
            await s.execute(
                text(
                    "SELECT event_type, payload FROM outbox_events "
                    "WHERE aggregate_id = :c AND event_type = 'handoff.updated'"
                ),
                {"c": first.conversation_id},
            )
        ).all()
    assert len(events) == 1
    assert "summary" not in events[0][1]


async def test_summary_worker_skips_short_conversation(session_factory):
    w = await make_world(session_factory)
    req = await request_for(session_factory, w, ("alo shop ơi",))
    async with session_factory() as s, s.begin():
        hid, _ = await open_handoff(s, w.tenant_id, req.conversation_id, source="customer_button")

    llm = FakeLLM([])
    worker = HandoffSummaryWorker(session_factory, llm, daily_cap_micro=200_000)
    assert await worker.process_once() is True
    assert llm.requests == []

    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT summary, summary_status FROM handoff_requests WHERE id = :id"),
                {"id": hid},
            )
        ).one()
    assert row == (None, "skipped")


async def test_summary_worker_marks_failed_on_llm_error_or_cap(session_factory):
    w = await make_world(session_factory)
    req = await request_for(session_factory, w, ("tin 1", "tin 2"))
    async with session_factory() as s, s.begin():
        hid, _ = await open_handoff(s, w.tenant_id, req.conversation_id, source="phrase")

    llm = FakeLLM([ProviderUnavailable("fake", "boom")])
    worker = HandoffSummaryWorker(session_factory, llm, daily_cap_micro=200_000)
    assert await worker.process_once() is True

    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT status, summary, summary_status FROM handoff_requests WHERE id = :id"),
                {"id": hid},
            )
        ).one()
    # Ticket vẫn mở bình thường, chỉ summary_status chuyển failed.
    assert row == ("open", None, "failed")
