"""Tầng `AutopilotStage` sau `AgentRunner` (thiết kế 06 A3): gửi webchat và lùi Learning."""

import json
from uuid import uuid4

from sqlalchemy import text

from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.prompts import PROMPT_VERSION
from app.modules.conversation_policy.domain.policy import suggested_config
from app.modules.conversation_policy.infrastructure.repository import publish, set_auto_send
from app.modules.learning.infrastructure.drafts import current_draft
from app.modules.outbound.stage import AutopilotStage

from .factories import insert_message
from .test_agent_runtime import classify, draft_out, make_runner, make_world, request_for


def _faq_draft(faq_id, reply="Dạ shop hỗ trợ đổi trong 7 ngày nếu còn tem ạ."):
    return draft_out(reply, sources=[("faq", str(faq_id))], needs_human=False, reason="")


async def _setup_learning_shop(
    session_factory,
    *,
    enable_faq: bool = True,
    auto_send: bool = True,
    prompt_ver_in_row: str = PROMPT_VERSION,
):
    w = await make_world(session_factory, rate="1.00")
    tpl = get_template("faq_sales_quote")
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE agents SET mode = 'learning' WHERE tenant_id = :t AND id = :a"),
            {"t": w.ctx.tenant_id, "a": w.agent_id},
        )
        await publish(s, w.ctx.tenant_id, suggested_config(), created_by=w.agent_id)
        await set_auto_send(s, w.ctx.tenant_id, auto_send)
        if enable_faq:
            cfg = {
                "template": tpl.key,
                "template_version": tpl.version,
                "prompt_version": prompt_ver_in_row,
            }
            await s.execute(
                text(
                    "INSERT INTO autopilot_intents "
                    "(tenant_id, agent_id, intent, state, config_key, source) "
                    "VALUES (:t, :a, 'faq', 'autopilot', CAST(:cfg AS jsonb), 'blind')"
                ),
                {"t": w.ctx.tenant_id, "a": w.agent_id, "cfg": json.dumps(cfg)},
            )
    return w


async def test_autopilot_stage_sends_bot_reply_once_and_hides_from_composer(session_factory):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
    stage = AutopilotStage(session_factory, runner.handle)
    req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))

    await stage.handle(req)

    # Chạy lại try_send (mô phỏng worker retry) ⇒ không gửi lần hai
    assert await stage.try_send(req) == "duplicate"

    async with session_factory() as s:
        msgs = (
            await s.execute(
                text(
                    "SELECT sender_type, draft_id, content FROM messages "
                    "WHERE tenant_id = :t AND conversation_id = :c ORDER BY sequence"
                ),
                {"t": w.ctx.tenant_id, "c": req.conversation_id},
            )
        ).all()
        step = (
            await s.execute(
                text(
                    "SELECT status, detail FROM agent_run_steps "
                    "WHERE tenant_id = :t AND name = 'send_gate'"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).one()
        draft_for_staff = await current_draft(s, w.ctx.tenant_id, req.conversation_id)

    assert len(msgs) == 2
    assert msgs[1].sender_type == "bot"
    assert msgs[1].draft_id is not None
    assert "7 ngày" in msgs[1].content
    assert step.status == "sent"
    assert draft_for_staff is None  # đã gửi rồi thì không hiện lại trong ô soạn tin


async def test_autopilot_stage_blocked_falls_back_to_learning(session_factory):
    # 1. auto_send = False ⇒ chặn kill_switch, nháp vẫn hiện cho nhân viên
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=False)
    runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
    stage = AutopilotStage(session_factory, runner.handle)
    req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))
    await stage.handle(req)

    async with session_factory() as s:
        bot_count = (
            await s.execute(
                text("SELECT count(*) FROM messages WHERE tenant_id = :t AND sender_type = 'bot'"),
                {"t": w.ctx.tenant_id},
            )
        ).scalar_one()
        step = (
            await s.execute(
                text(
                    "SELECT status, detail FROM agent_run_steps "
                    "WHERE tenant_id = :t AND name = 'send_gate'"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).one()
        draft_for_staff = await current_draft(s, w.ctx.tenant_id, req.conversation_id)

    assert bot_count == 0
    assert step.status == "blocked"
    assert step.detail["reason"] == "kill_switch"
    assert draft_for_staff is not None  # lùi về Learning

    # 2. Đổi PROMPT_VERSION ⇒ chặn config_changed
    w2 = await _setup_learning_shop(
        session_factory, enable_faq=True, auto_send=True, prompt_ver_in_row="old-prompt"
    )
    runner2, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w2.faq_id)])
    stage2 = AutopilotStage(session_factory, runner2.handle)
    req2 = await request_for(session_factory, w2, texts=("Đổi trả thế nào?",))
    await stage2.handle(req2)

    async with session_factory() as s:
        step2 = (
            await s.execute(
                text(
                    "SELECT status, detail FROM agent_run_steps "
                    "WHERE tenant_id = :t AND name = 'send_gate'"
                ),
                {"t": w2.ctx.tenant_id},
            )
        ).one()
    assert step2.status == "blocked"
    assert step2.detail["reason"] == "config_changed"

    # 3. Khách nhắn thêm ngay trước bước gửi ⇒ chặn stale
    w3 = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    runner3, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w3.faq_id)])
    req3 = await request_for(session_factory, w3, texts=("Đổi trả thế nào?",))
    await runner3.handle(req3)
    async with session_factory() as s, s.begin():
        await insert_message(
            s, w3.ctx, req3.conversation_id, w3.conn, sequence=2, content="À thêm câu nữa"
        )
        await s.execute(
            text("UPDATE conversations SET last_sequence = 2 WHERE tenant_id = :t AND id = :c"),
            {"t": w3.ctx.tenant_id, "c": req3.conversation_id},
        )
    stage3 = AutopilotStage(session_factory, runner3.handle)
    assert await stage3.try_send(req3) == "stale"


async def test_autopilot_stage_blocks_reply_while_order_flow_is_active(session_factory):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    runner, _ = make_runner(session_factory, [classify("faq"), _faq_draft(w.faq_id)])
    req = await request_for(session_factory, w, texts=("Đổi trả thế nào?",))
    await runner.handle(req)
    # Runner ghi bước này khi hội thoại có phiếu đặt hàng dở, dù ý định là gì.
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO agent_run_steps "
                "(tenant_id, run_id, step_index, kind, name, status, latency_ms, detail) "
                "SELECT tenant_id, id, 99, 'rule', 'order_flow', 'active', 0, '{}'::jsonb "
                "FROM agent_runs WHERE tenant_id = :t AND conversation_id = :c"
            ),
            {"t": w.ctx.tenant_id, "c": req.conversation_id},
        )
    assert await AutopilotStage(session_factory, runner.handle).try_send(req) == "order_flow"


async def test_autopilot_blocks_reply_about_a_different_product(session_factory):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    plain, mini = uuid4(), uuid4()
    async with session_factory() as s, s.begin():
        for pid, sku, name, price in (
            (plain, "KN-006", "Bình giữ nhiệt mini", 190000),
            (mini, "KN-008", "Bình giữ nhiệt mini Mini", 130000),
        ):
            await s.execute(
                text(
                    "INSERT INTO catalog_items (id, tenant_id, sku, name, price_vnd, search_text, "
                    "external_id) VALUES (:id, :t, :sku, :n, :p, :n, :sku)"
                ),
                {"id": pid, "t": w.ctx.tenant_id, "sku": sku, "n": name, "p": price},
            )
    # Ca gửi sai đo được: khách hỏi "mini Mini", nháp trả lời bằng giá thật của "mini".
    wrong = draft_out(
        "Dạ bình giữ nhiệt mini loại 500ml giá 190.000đ ạ.",
        sources=[("product", str(plain))],
        needs_human=False,
    )
    runner, _ = make_runner(session_factory, [classify("faq"), wrong])
    asked = ("Cho minh hoi gia Binh giu nhiet mini Mini",)
    req = await request_for(session_factory, w, texts=asked)
    await runner.handle(req)
    async with session_factory() as s, s.begin():  # bỏ qua kiểm nguồn để tới đúng bước này
        await s.execute(
            text("UPDATE agent_drafts SET grounded = true WHERE tenant_id = :t"),
            {"t": w.ctx.tenant_id},
        )
        await s.execute(
            text(
                "UPDATE agent_run_steps SET detail = '{\"problems\": []}'::jsonb "
                "WHERE tenant_id = :t AND kind = 'validate' AND name = 'draft'"
            ),
            {"t": w.ctx.tenant_id},
        )
    stage = AutopilotStage(session_factory, runner.handle)
    assert await stage.try_send(req) == "product_mismatch"

    async with session_factory() as s, s.begin():  # nháp trích đúng sản phẩm khách gọi ⇒ gửi
        await s.execute(
            text("UPDATE agent_drafts SET sources = CAST(:src AS jsonb) WHERE tenant_id = :t"),
            {"t": w.ctx.tenant_id, "src": json.dumps([{"type": "product", "id": str(mini)}])},
        )
    assert await stage.try_send(req) == "sent"
