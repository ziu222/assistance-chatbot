"""Theo dõi sau gửi (`ai_reply_followups`), chấm mẫu và tự hạ cấp (thiết kế 06 A4)."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.outbound.infrastructure.monitoring import (
    record_customer_reaction_if_applicable,
    record_staff_correction_if_applicable,
)
from app.modules.outbound.stage import AutopilotStage

from .test_agent_runtime import classify, make_runner, request_for
from .test_autopilot_stage import _faq_draft, _setup_learning_shop
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def test_config_change_demotes_intent_with_signal(session_factory):
    w = await _setup_learning_shop(
        session_factory, enable_faq=True, auto_send=True, prompt_ver_in_row="old-prompt"
    )
    runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
    stage = AutopilotStage(session_factory, runner.handle)
    req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))
    await stage.handle(req)

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT state, demote_reason FROM autopilot_intents "
                    "WHERE tenant_id = :t AND agent_id = :a AND intent = 'faq'"
                ),
                {"t": w.ctx.tenant_id, "a": w.agent_id},
            )
        ).one()
        sig = (
            await s.execute(
                text(
                    "SELECT payload FROM learning_signals "
                    "WHERE tenant_id = :t AND kind = 'autopilot_demoted'"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).scalar_one()
    assert row.state == "learning"
    assert row.demote_reason == "config_changed"
    assert sig["reason"] == "config_changed"


async def test_staff_amount_corrections_above_threshold_auto_demote(session_factory):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    # Gửi 2 tin bot ở 2 hội thoại khác nhau rồi nhân viên đính chính đổi số tiền ở cả 2
    for _ in range(2):
        runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
        stage = AutopilotStage(session_factory, runner.handle)
        req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))
        await stage.handle(req)
        async with session_factory() as s, s.begin():
            seq = (
                await s.execute(
                    text(
                        "UPDATE conversations SET last_sequence = last_sequence + 1 "
                        "WHERE id = :c RETURNING last_sequence"
                    ),
                    {"c": req.conversation_id},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                    "external_message_id, direction, sender_type, content, status, sequence, "
                    "occurred_at) VALUES (:id, :t, :conn, :c, :ext, 'outbound', 'human', "
                    "'Xin lỗi chị, phí đổi là 30.000đ nhé.', 'sent', :seq, now())"
                ),
                {
                    "id": uuid4(),
                    "t": w.ctx.tenant_id,
                    "conn": w.conn,
                    "c": req.conversation_id,
                    "ext": str(uuid4()),
                    "seq": seq,
                },
            )
            await record_staff_correction_if_applicable(
                s,
                w.ctx.tenant_id,
                req.conversation_id,
                staff_sequence=seq,
                staff_text="Xin lỗi chị, phí đổi là 30.000đ nhé.",
            )

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT state, demote_reason FROM autopilot_intents "
                    "WHERE tenant_id = :t AND agent_id = :a AND intent = 'faq'"
                ),
                {"t": w.ctx.tenant_id, "a": w.agent_id},
            )
        ).one()
    assert row.state == "learning"
    assert row.demote_reason == "staff_correction_rate"


async def test_owner_review_replies_and_wrong_fact_label_demotes_immediately(
    new_client, session_factory
):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
    stage = AutopilotStage(session_factory, runner.handle)
    req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))
    await stage.handle(req)

    # Ghi nhận phản ứng xấu khi khách phàn nàn ở lượt sau
    async with session_factory() as s, s.begin():
        await record_customer_reaction_if_applicable(
            s,
            w.ctx.tenant_id,
            req.conversation_id,
            next_intents=("complaint",),
            next_customer_texts=("Trả lời gì kỳ vậy?",),
            run_from_sequence=3,
        )

    owner, *_ = await logged_in(new_client, session_factory, role="owner", ctx=w.ctx)
    listed = (await owner.get("/api/v1/autopilot/replies?days=7")).json()
    assert len(listed["items"]) == 1
    item = listed["items"][0]
    assert item["intent"] == "faq"
    assert any(f["kind"] == "customer_reaction" for f in item["followups"])

    r = await owner.post(
        f"/api/v1/autopilot/replies/{item['message_id']}/label",
        json={"label": "wrong", "wrong_fact": True},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["demoted_reason"] == "owner_label_wrong"

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT state, demote_reason FROM autopilot_intents "
                    "WHERE tenant_id = :t AND agent_id = :a AND intent = 'faq'"
                ),
                {"t": w.ctx.tenant_id, "a": w.agent_id},
            )
        ).one()
    assert row.state == "learning"
    assert row.demote_reason == "owner_label_wrong"
