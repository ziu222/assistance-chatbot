"""Cổng eval với bộ ca chuẩn theo ý định Autopilot (thiết kế 06 §6.3, A5)."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_management.infrastructure.repository import insert_agent
from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.llm.fake import FakeLLM
from app.modules.agent_runtime.llm.port import LLMRequest, LLMResult, Target, Usage
from app.modules.agent_runtime.llm.routes import load_routes
from app.modules.agent_runtime.prompts import PROMPT_VERSION
from app.modules.knowledge.domain.releases import SnapshotItem, render_knowledge
from app.modules.knowledge.infrastructure.releases import active_snapshot, publish_from_tables
from app.modules.learning.domain.eval_gate import (
    EvalDraftOut,
    EvalSource,
    build_standard_amount_case,
)
from app.modules.learning.infrastructure.review import (
    evaluate_manual_releases,
    run_eval_gate_for_candidate,
)
from app.modules.outbound.domain.autopilot import primary_models

from .test_staff_api import CSRF, logged_in

_PRICED_TARGET = Target("openai", "gpt-4.1-mini", max_tokens=512)


def _text_res(reply: str) -> LLMResult:
    return LLMResult("end_turn", reply, None, (), Usage(input=80, output=40), _PRICED_TARGET)


def _draft_res(draft: EvalDraftOut) -> LLMResult:
    return LLMResult("end_turn", "", draft, (), Usage(input=80, output=40), _PRICED_TARGET)


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def _enable_autopilot(
    session_factory, ctx, conn: UUID, user_id: UUID, intents: tuple[str, ...]
) -> UUID:
    async with session_factory() as s, s.begin():
        agent_id = await insert_agent(
            s,
            tenant_id=ctx.tenant_id,
            connection_id=conn,
            name="Bot Shop",
            template_key="faq_sales_quote",
            template_version=5,
            config={},
            created_by=user_id,
        )
        assert agent_id is not None
        await s.execute(
            text("UPDATE agents SET mode = 'learning' WHERE tenant_id = :t AND id = :a"),
            {"t": ctx.tenant_id, "a": agent_id},
        )
        cfg = {
            "template": "faq_sales_quote",
            "template_version": 5,
            "prompt_version": PROMPT_VERSION,
            "models": primary_models(load_routes(), get_template("faq_sales_quote", 5)),
        }
        for intent in intents:
            await s.execute(
                text(
                    "INSERT INTO autopilot_intents "
                    "(tenant_id, agent_id, intent, state, config_key, source, enabled_by, "
                    " enabled_at) "
                    "VALUES (:t, :a, :i, 'autopilot', CAST(:cfg AS jsonb), 'blind', :by, now())"
                ),
                {
                    "t": ctx.tenant_id,
                    "a": agent_id,
                    "i": intent,
                    "cfg": json.dumps(cfg),
                    "by": user_id,
                },
            )
    return agent_id


async def test_learning_release_blocked_when_standard_autopilot_case_fails(
    new_client, session_factory
):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    await _enable_autopilot(session_factory, ctx, conn, owner_id, ("faq",))

    hours_id = uuid4()
    cand_id = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO faq_entries (id, tenant_id, question, answer, status) "
                "VALUES (:id, :t, 'Giờ mở cửa?', 'Shop mở cửa 8h - 21h hằng ngày.', 'approved')"
            ),
            {"id": hours_id, "t": ctx.tenant_id},
        )
        await publish_from_tables(s, ctx.tenant_id, reason="manual_edit", created_by=owner_id)
        await s.execute(
            text(
                "INSERT INTO knowledge_candidates "
                "(id, tenant_id, kind, intent, topic, fingerprint, proposal, replaces, "
                " evidence, checks, status, reviewed_by, reviewed_at) "
                "VALUES (:id, :t, 'faq_new', 'faq', 'shipping', :fp, CAST(:prop AS jsonb), NULL, "
                " '[]'::jsonb, '{\"passed\": true}'::jsonb, 'approved', :by, now())"
            ),
            {
                "id": cand_id,
                "t": ctx.tenant_id,
                "fp": cand_id.hex,
                "prop": json.dumps(
                    {"question": "Phí ship bao nhiêu?", "answer": "Phí ship 30.000đ toàn quốc."},
                    ensure_ascii=False,
                ),
                "by": owner_id,
            },
        )

    # 1. Ca bài học đạt nhưng ca chuẩn hành vi của `faq` bịa số tiền + chuyển người thừa ⇒ CHẶN
    def failing_handler(req: LLMRequest) -> LLMResult:
        if req.output_schema is None:
            return _text_res("Dạ phí ship 30.000đ toàn quốc ạ.")
        return _draft_res(
            EvalDraftOut(
                reply="Phí ship 99.000đ, để nhân viên kiểm tra nhé.",
                sources=[],
                needs_human=True,
                reason="no_source",
            )
        )

    rep_fail = await run_eval_gate_for_candidate(
        session_factory,
        FakeLLM([failing_handler, failing_handler, failing_handler]),
        tenant_id=ctx.tenant_id,
        candidate_id=cand_id,
        daily_cap_micro=500_000,
    )
    assert rep_fail is not None and rep_fail["passed"] is False
    assert any(
        fc["source"] == "standard" and fc["intent"] == "faq" for fc in rep_fail["failed_cases"]
    )

    async with session_factory() as s:
        snap_before = render_knowledge(await active_snapshot(s, ctx.tenant_id)).block
        status = (
            await s.execute(
                text("SELECT status FROM knowledge_candidates WHERE id = :id"), {"id": cand_id}
            )
        ).scalar_one()
    assert status == "eval_failed"
    assert "30.000đ" not in snap_before  # KHÔNG phát hành bản từ Learning khi ca chuẩn hỏng

    # 2. Duyệt lại và ca chuẩn đạt (có nguồn hợp lệ, không bịa số, không chuyển người) ⇒ phát hành
    assert (
        await owner.post(f"/api/v1/learning/candidates/{cand_id}/approve", headers=CSRF)
    ).status_code == 200

    def passing_handler(req: LLMRequest) -> LLMResult:
        if req.output_schema is None:
            return _text_res("Dạ phí ship 30.000đ toàn quốc ạ.")
        return _draft_res(
            EvalDraftOut(
                reply="Dạ shop mở cửa 8h - 21h hằng ngày ạ.",
                sources=[EvalSource(type="faq", id=str(hours_id))],
                needs_human=False,
            )
        )

    rep_ok = await run_eval_gate_for_candidate(
        session_factory,
        FakeLLM([passing_handler, passing_handler, passing_handler]),
        tenant_id=ctx.tenant_id,
        candidate_id=cand_id,
        daily_cap_micro=500_000,
    )
    assert rep_ok is not None and rep_ok["passed"] is True
    async with session_factory() as s:
        snap_after = render_knowledge(await active_snapshot(s, ctx.tenant_id)).block
    assert "30.000đ" in snap_after


async def test_manual_edit_publishes_and_demotes_only_regressed_intent(new_client, session_factory):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    agent_id = await _enable_autopilot(session_factory, ctx, conn, owner_id, ("faq", "greeting"))

    # Chủ shop sửa/thêm FAQ bằng tay ⇒ phát hành ngay (chủ shop là nguồn sự thật)
    r = await owner.post(
        "/api/v1/knowledge/faqs",
        json={
            "question": "Chính sách đổi trả?",
            "answer": "Đổi trả trong 7 ngày nếu còn tem.",
            "status": "approved",
        },
        headers=CSRF,
    )
    assert r.status_code == 201, r.text

    # Cổng eval chạy bộ ca chuẩn: `faq` hỏng (chuyển người thừa, thiếu nguồn), `greeting` đạt
    def mixed_handler(req: LLMRequest) -> LLMResult:
        prompt = req.turns[0].text
        if "<question>Chính sách đổi trả?</question>" in prompt:
            return _draft_res(
                EvalDraftOut(
                    reply="Để nhân viên kiểm tra giúp bạn nhé.",
                    sources=[],
                    needs_human=True,
                    reason="policy",
                )
            )
        return _draft_res(
            EvalDraftOut(
                reply="Xin chào bạn, shop có thể hỗ trợ gì ạ?",
                sources=[],
                needs_human=False,
            )
        )

    evaluated = await evaluate_manual_releases(
        session_factory,
        FakeLLM([mixed_handler, mixed_handler, mixed_handler]),
        daily_cap_micro=500_000,
    )
    assert evaluated == 1

    async with session_factory() as s:
        # Bản sửa tay VẪN đang hiệu lực
        snap = render_knowledge(await active_snapshot(s, ctx.tenant_id)).block
        rows = (
            await s.execute(
                text(
                    "SELECT intent, state, demote_reason FROM autopilot_intents "
                    "WHERE tenant_id = :t AND agent_id = :a ORDER BY intent"
                ),
                {"t": ctx.tenant_id, "a": agent_id},
            )
        ).all()
        rel = (
            await s.execute(
                text(
                    "SELECT eval_run_id, eval_report FROM knowledge_releases "
                    "WHERE tenant_id = :t ORDER BY number DESC LIMIT 1"
                ),
                {"t": ctx.tenant_id},
            )
        ).one()
        sig = (
            await s.execute(
                text(
                    "SELECT payload FROM learning_signals "
                    "WHERE tenant_id = :t AND kind = 'autopilot_demoted'"
                ),
                {"t": ctx.tenant_id},
            )
        ).scalar_one()

    assert "Đổi trả trong 7 ngày" in snap
    by_intent = {row.intent: (row.state, row.demote_reason) for row in rows}
    assert by_intent["faq"] == ("learning", "eval_regressed")
    assert by_intent["greeting"] == ("autopilot", None)
    assert rel.eval_run_id is not None
    assert rel.eval_report["demoted_intents"] == ["faq"]
    assert sig["intent"] == "faq" and sig["reason"] == "eval_regressed"


async def test_owner_changing_shipping_fee_retires_bound_standard_case_without_false_demotion(
    new_client, session_factory
):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    agent_id = await _enable_autopilot(session_factory, ctx, conn, owner_id, ("faq",))

    r1 = await owner.post(
        "/api/v1/knowledge/faqs",
        json={
            "question": "Phí ship?",
            "answer": "Phí ship 30.000đ toàn quốc.",
            "status": "approved",
        },
        headers=CSRF,
    )
    assert r1.status_code == 201, r1.text
    faq_id = r1.json()["id"]

    # Gắn một ca chuẩn kiểm số tiền (30.000đ) có entry_id + entry_hash của FAQ phí ship
    old_item = SnapshotItem(faq_id, "Phí ship?", "Phí ship 30.000đ toàn quốc.")
    std_case_id = uuid4()
    std_spec = build_standard_amount_case("faq", old_item, case_id=str(std_case_id))
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO eval_cases "
                "(id, tenant_id, source, intent, question, expected, active, entry_id, entry_hash) "
                "VALUES (:id, :t, 'standard', 'faq', :q, CAST(:exp AS jsonb), true, :eid, :eh)"
            ),
            {
                "id": std_case_id,
                "t": ctx.tenant_id,
                "q": std_spec.question,
                "exp": json.dumps(std_spec.expected_dict(), ensure_ascii=False),
                "eid": UUID(faq_id),
                "eh": std_spec.entry_hash,
            },
        )

    # Chủ shop đổi phí ship từ 30.000đ lên 35.000đ
    r2 = await owner.patch(
        f"/api/v1/knowledge/faqs/{faq_id}",
        json={"answer": "Phí ship 35.000đ toàn quốc."},
        headers=CSRF,
    )
    assert r2.status_code == 200, r2.text

    # Ca cũ đòi 30.000đ tự hết hiệu lực (không chạy); chỉ 1 ca hành vi chạy trên giá mới 35.000đ
    llm = FakeLLM(
        [
            _draft_res(
                EvalDraftOut(
                    reply="Dạ phí ship hiện là 35.000đ toàn quốc ạ.",
                    sources=[EvalSource(type="faq", id=faq_id)],
                    needs_human=False,
                )
            )
        ]
    )
    evaluated = await evaluate_manual_releases(session_factory, llm, daily_cap_micro=500_000)
    assert evaluated == 1
    assert len(llm.requests) == 1  # ca 30k cũ không bị chạy

    async with session_factory() as s:
        intent_row = (
            await s.execute(
                text(
                    "SELECT state, demote_reason FROM autopilot_intents "
                    "WHERE tenant_id = :t AND agent_id = :a AND intent = 'faq'"
                ),
                {"t": ctx.tenant_id, "a": agent_id},
            )
        ).one()
        case_active = (
            await s.execute(
                text("SELECT active FROM eval_cases WHERE id = :id"), {"id": std_case_id}
            )
        ).scalar_one()
    assert case_active is False  # ca 30k đã được tắt
    assert intent_row.state == "autopilot"  # KHÔNG bị hạ cấp oan!
    assert intent_row.demote_reason is None


async def test_restore_release_retriggers_standard_eval(new_client, session_factory):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    await _enable_autopilot(session_factory, ctx, conn, owner_id, ("faq",))

    r1 = await owner.post(
        "/api/v1/knowledge/faqs",
        json={"question": "Phí ship?", "answer": "Phí ship 30.000đ.", "status": "approved"},
        headers=CSRF,
    )
    faq_id = r1.json()["id"]
    await owner.patch(
        f"/api/v1/knowledge/faqs/{faq_id}",
        json={"answer": "Phí ship 35.000đ."},
        headers=CSRF,
    )
    ok_llm = FakeLLM(
        [
            _draft_res(
                EvalDraftOut(
                    reply="Dạ phí ship 35.000đ ạ.",
                    sources=[EvalSource(type="faq", id=faq_id)],
                    needs_human=False,
                )
            )
        ]
    )
    assert await evaluate_manual_releases(session_factory, ok_llm, daily_cap_micro=500_000) == 1

    # Quay lại bản #1 ⇒ xóa eval_run_id để cổng eval kiểm lại bộ ca chuẩn
    restored = await owner.post("/api/v1/knowledge/releases/1/restore", headers=CSRF)
    assert restored.status_code == 200
    assert restored.json()["eval_run_id"] is None

    recheck_llm = FakeLLM(
        [
            _draft_res(
                EvalDraftOut(
                    reply="Dạ phí ship 30.000đ ạ.",
                    sources=[EvalSource(type="faq", id=faq_id)],
                    needs_human=False,
                )
            )
        ]
    )
    assert (
        await evaluate_manual_releases(session_factory, recheck_llm, daily_cap_micro=500_000) == 1
    )


async def test_product_standard_cases_see_the_catalog(new_client, session_factory):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    agent_id = await _enable_autopilot(session_factory, ctx, conn, owner_id, ("product_info",))
    product_id = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO catalog_items (id, tenant_id, sku, name, price_vnd, search_text, "
                "external_id) VALUES (:id, :t, 'AK-1', 'Áo khoác gió', 350000, 'ao khoac gio', "
                "'AK-1')"
            ),
            {"id": product_id, "t": ctx.tenant_id},
        )
    r = await owner.post(
        "/api/v1/knowledge/faqs",
        json={"question": "Phí ship?", "answer": "Ship 30.000đ", "status": "approved"},
        headers=CSRF,
    )
    assert r.status_code == 201, r.text

    # Model eval không có tool: chỉ trả lời được nếu thấy sản phẩm trong prompt.
    def answers_from_catalog(req: LLMRequest) -> LLMResult:
        prompt = req.turns[0].text
        if f'<product id="{product_id}" price_vnd="350000">Áo khoác gió</product>' not in prompt:
            return _draft_res(EvalDraftOut(reply="Để nhân viên hỗ trợ ạ.", needs_human=True))
        return _draft_res(
            EvalDraftOut(
                reply="Áo khoác gió giá 350.000đ ạ.",
                sources=[EvalSource(type="product", id=str(product_id))],
            )
        )

    assert (
        await evaluate_manual_releases(
            session_factory, FakeLLM([answers_from_catalog]), daily_cap_micro=500_000
        )
        == 1
    )
    async with session_factory() as s:
        state = (
            await s.execute(
                text("SELECT state FROM autopilot_intents WHERE tenant_id = :t AND agent_id = :a"),
                {"t": ctx.tenant_id, "a": agent_id},
            )
        ).scalar_one()
    assert state == "autopilot"
