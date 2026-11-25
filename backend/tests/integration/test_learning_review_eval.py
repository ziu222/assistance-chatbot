"""Duyệt ứng viên, cổng eval hồi quy và phát hành bản tri thức mới (thiết kế 05 L4)."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_runtime.context import build_context
from app.modules.agent_runtime.llm.fake import FakeLLM
from app.modules.agent_runtime.llm.port import LLMResult, ProviderUnavailable, Target, Usage
from app.modules.knowledge.domain.releases import render_knowledge
from app.modules.knowledge.infrastructure.releases import active_snapshot, publish_from_tables
from app.modules.learning.infrastructure.review import run_eval_gate_for_candidate

from .factories import insert_message, make_conversation, make_customer
from .test_staff_api import CSRF, logged_in

_PRICED_TARGET = Target("openai", "gpt-4.1-mini", max_tokens=512)


def _eval_reply(reply: str) -> LLMResult:
    return LLMResult("end_turn", reply, None, (), Usage(input=80, output=40), _PRICED_TARGET)


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def _seed_candidate(
    session_factory,
    tenant_id,
    *,
    replaces,
    question="Giá áo khoác gió bao nhiêu?",
    answer="Áo khoác gió hiện giảm còn 320.000đ.",
    passed=True,
):
    cid = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO knowledge_candidates "
                "(id, tenant_id, kind, intent, topic, fingerprint, proposal, replaces, "
                " evidence, checks, status) "
                "VALUES (:id, :t, 'faq_update', 'price_quote', 'other', :fp, "
                " CAST(:prop AS jsonb), :rep, '[]'::jsonb, CAST(:chk AS jsonb), 'pending')"
            ),
            {
                "id": cid,
                "t": tenant_id,
                "fp": cid.hex,
                "prop": json.dumps({"question": question, "answer": answer}, ensure_ascii=False),
                "rep": replaces,
                "chk": json.dumps({"passed": passed, "pii": [] if passed else ["phone"]}),
            },
        )
    return cid


async def test_approve_then_eval_pass_publishes_and_eval_fail_blocks(new_client, session_factory):
    owner, ctx, _, conn = await logged_in(new_client, session_factory, role="owner")
    faq_id = uuid4()
    async with session_factory() as s, s.begin():
        cust = await make_customer(s, ctx)
        conv_id = await make_conversation(s, ctx, cust, conn)
        await insert_message(s, ctx, conv_id, conn, sequence=1, content="Áo khoác gió bao nhiêu?")
        await s.execute(
            text(
                "INSERT INTO faq_entries (id, tenant_id, question, answer, status) "
                "VALUES (:id, :t, 'Giá áo khoác gió?', 'Áo khoác gió giá 350.000đ', 'approved')"
            ),
            {"id": faq_id, "t": ctx.tenant_id},
        )
        await publish_from_tables(s, ctx.tenant_id, reason="manual_edit", created_by=None)

    # 1. Ứng viên không đạt kiểm tra code bị chặn duyệt (422)
    blocked_id = await _seed_candidate(
        session_factory, ctx.tenant_id, replaces=faq_id, passed=False
    )
    r_block = await owner.post(f"/api/v1/learning/candidates/{blocked_id}/approve", headers=CSRF)
    assert r_block.status_code == 422

    # 2. Nhân viên thường không có quyền duyệt (403)
    agent, *_ = await logged_in(new_client, session_factory, role="agent", ctx=ctx)
    cand_id = await _seed_candidate(session_factory, ctx.tenant_id, replaces=faq_id, passed=True)
    assert (
        await agent.post(f"/api/v1/learning/candidates/{cand_id}/approve", headers=CSRF)
    ).status_code == 403

    # 3. Chủ shop duyệt ⇒ status = approved
    r_ok = await owner.post(f"/api/v1/learning/candidates/{cand_id}/approve", headers=CSRF)
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "approved"

    # 4. Eval hỏng (vẫn báo giá cũ 350.000đ) ⇒ eval_failed, KHÔNG phát hành bản mới
    llm_fail = FakeLLM([_eval_reply("Dạ áo khoác gió giá 350.000đ ạ.")])
    rep_fail = await run_eval_gate_for_candidate(
        session_factory,
        llm_fail,
        tenant_id=ctx.tenant_id,
        candidate_id=cand_id,
        daily_cap_micro=500_000,
    )
    assert rep_fail is not None and rep_fail["passed"] is False
    async with session_factory() as s:
        ctx_before = await build_context(s, ctx.tenant_id, conv_id, 1, 1)
    assert "350.000đ" in ctx_before.knowledge_block

    # 5. Duyệt lại, eval đạt (320.000đ) ⇒ published + lưu eval_cases + cập nhật context
    assert (
        await owner.post(f"/api/v1/learning/candidates/{cand_id}/approve", headers=CSRF)
    ).status_code == 200
    llm_pass = FakeLLM([_eval_reply("Dạ áo khoác gió hiện giảm còn 320.000đ ạ.")])
    rep_pass = await run_eval_gate_for_candidate(
        session_factory,
        llm_pass,
        tenant_id=ctx.tenant_id,
        candidate_id=cand_id,
        daily_cap_micro=500_000,
    )
    assert rep_pass is not None and rep_pass["passed"] is True

    async with session_factory() as s:
        ctx_after = await build_context(s, ctx.tenant_id, conv_id, 1, 1)
        eval_cnt = (
            await s.execute(
                text("SELECT count(*) FROM eval_cases WHERE tenant_id = :t AND candidate_id = :c"),
                {"t": ctx.tenant_id, "c": cand_id},
            )
        ).scalar_one()
    assert "320.000đ" in ctx_after.knowledge_block
    assert "350.000đ" not in ctx_after.knowledge_block
    assert eval_cnt == 1


async def test_reject_candidate_records_reason_and_signal(new_client, session_factory):
    owner, ctx, _, _ = await logged_in(new_client, session_factory, role="owner")
    cand_id = await _seed_candidate(session_factory, ctx.tenant_id, replaces=None, passed=True)
    r = await owner.post(
        f"/api/v1/learning/candidates/{cand_id}/reject",
        json={"reason": "Giá khuyến mãi tạm thời"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "Giá khuyến mãi tạm thời"


async def _approved(session_factory, tenant_id, **kw):
    cid = await _seed_candidate(session_factory, tenant_id, **kw)
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE knowledge_candidates SET status = 'approved', reviewed_at = now() "
                "WHERE id = :id"
            ),
            {"id": cid},
        )
    return cid


async def _faq_tenant(new_client, session_factory, answer="Áo khoác gió giá 350.000đ"):
    _, ctx, _, conn = await logged_in(new_client, session_factory, role="owner")
    faq_id = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO faq_entries (id, tenant_id, question, answer, status) "
                "VALUES (:id, :t, 'Giá áo khoác gió?', :a, 'approved')"
            ),
            {"id": faq_id, "t": ctx.tenant_id, "a": answer},
        )
        await publish_from_tables(s, ctx.tenant_id, reason="manual_edit", created_by=None)
    return ctx, faq_id


async def _gate(session_factory, llm, tenant_id, cid):
    return await run_eval_gate_for_candidate(
        session_factory, llm, tenant_id=tenant_id, candidate_id=cid, daily_cap_micro=500_000
    )


async def _status(session_factory, cid):
    async with session_factory() as s:
        return (
            await s.execute(
                text("SELECT status, eval_attempts FROM knowledge_candidates WHERE id = :id"),
                {"id": cid},
            )
        ).one()


async def test_second_price_update_retires_previous_lesson_case(new_client, session_factory):
    ctx, faq_id = await _faq_tenant(new_client, session_factory)
    first = await _approved(session_factory, ctx.tenant_id, replaces=faq_id)
    ok = FakeLLM([_eval_reply("Dạ áo khoác gió hiện giảm còn 320.000đ ạ.")])
    assert (await _gate(session_factory, ok, ctx.tenant_id, first))["passed"] is True

    second = await _approved(
        session_factory, ctx.tenant_id, replaces=faq_id, answer="Áo khoác gió còn 300.000đ."
    )
    llm = FakeLLM([_eval_reply("Dạ áo khoác gió còn 300.000đ ạ.")])
    report = await _gate(session_factory, llm, ctx.tenant_id, second)

    # Ca cũ đòi 320.000đ không chạy nữa (chỉ 1 lời gọi) và bị tắt khi phát hành.
    assert report is not None and report["passed"] is True
    assert len(llm.requests) == 1
    async with session_factory() as s:
        cases = (
            await s.execute(
                text("SELECT candidate_id, active FROM eval_cases WHERE tenant_id = :t"),
                {"t": ctx.tenant_id},
            )
        ).all()
    assert sorted((c.candidate_id == second, c.active) for c in cases) == [
        (False, False),
        (True, True),
    ]


async def test_knowledge_change_during_eval_does_not_publish_stale_snapshot(
    new_client, session_factory
):
    ctx, faq_id = await _faq_tenant(new_client, session_factory)
    cid = await _approved(session_factory, ctx.tenant_id, replaces=faq_id)

    async def owner_edits_meanwhile(_req):
        async with session_factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO faq_entries (id, tenant_id, question, answer, status) "
                    "VALUES (:id, :t, 'Phí ship?', 'Ship 30.000đ', 'approved')"
                ),
                {"id": uuid4(), "t": ctx.tenant_id},
            )
            await publish_from_tables(s, ctx.tenant_id, reason="manual_edit", created_by=None)
        return _eval_reply("Dạ áo khoác gió hiện giảm còn 320.000đ ạ.")

    assert (
        await _gate(session_factory, FakeLLM([owner_edits_meanwhile]), ctx.tenant_id, cid) is None
    )
    assert (await _status(session_factory, cid)).status == "approved"  # lượt sau chạy lại

    rerun = FakeLLM([_eval_reply("Dạ áo khoác gió hiện giảm còn 320.000đ ạ.")])
    assert (await _gate(session_factory, rerun, ctx.tenant_id, cid))["passed"] is True
    async with session_factory() as s:
        block = render_knowledge(await active_snapshot(s, ctx.tenant_id)).block
    assert "320.000đ" in block and "Ship 30.000đ" in block


async def test_llm_failure_records_partial_usage_and_caps_retries(new_client, session_factory):
    ctx, faq_id = await _faq_tenant(new_client, session_factory)
    # Một ca "giá sách" còn hiệu lực ⇒ cổng chạy 2 ca; ca thứ hai lỗi provider.
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO eval_cases (id, tenant_id, source, question, expected, active) "
                "VALUES (:id, :t, 'lesson', 'Sách giá bao nhiêu?', '{}'::jsonb, true)"
            ),
            {"id": uuid4(), "t": ctx.tenant_id},
        )
    cid = await _approved(session_factory, ctx.tenant_id, replaces=faq_id)

    for attempt in (1, 2, 3):
        llm = FakeLLM(
            [_eval_reply("Dạ còn 320.000đ ạ."), ProviderUnavailable("anthropic", "server_error")]
        )
        assert await _gate(session_factory, llm, ctx.tenant_id, cid) is None
        row = await _status(session_factory, cid)
        assert row.eval_attempts == attempt
    assert row.status == "eval_failed"
    async with session_factory() as s:
        used = (
            await s.execute(
                text("SELECT count(*) FROM usage_events WHERE tenant_id = :t"),
                {"t": ctx.tenant_id},
            )
        ).scalar_one()
    assert used == 3  # lời gọi thành công của mỗi lượt vẫn bị tính vào trần chi phí
