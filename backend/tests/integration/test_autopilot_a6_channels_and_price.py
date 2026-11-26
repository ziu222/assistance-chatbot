"""Kênh Messenger 24h, Zalo OA và `price_quote` kiểm độ mới catalog (thiết kế 06 A6)."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.llm.routes import load_routes
from app.modules.agent_runtime.prompts import PROMPT_VERSION
from app.modules.outbound.ai_reply import send_ai_reply
from app.modules.outbound.domain.autopilot import primary_models
from app.modules.outbound.stage import AutopilotStage, _check_catalog_stale

from .factories import make_conversation, make_customer, make_tenant
from .test_agent_runtime import classify, draft_out, make_runner, request_for
from .test_autopilot_api import _seed_agent
from .test_autopilot_stage import _setup_learning_shop
from .test_shadow_summary import add
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def _setup_channel_conv(
    session_factory,
    *,
    provider: str,
    customer_at: datetime,
    config: dict | None = None,
    prior_outbound_count: int = 0,
):
    async with session_factory() as s, s.begin():
        ctx = await make_tenant(s)
        conn_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO channel_connections "
                "(id, tenant_id, provider, status, config) "
                "VALUES (:id, :t, :p, 'active', CAST(:cfg AS jsonb))"
            ),
            {"id": conn_id, "t": ctx.tenant_id, "p": provider, "cfg": json.dumps(config or {})},
        )
        cust = await make_customer(s, ctx)
        conv = await make_conversation(s, ctx, cust, conn_id, status="open")
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                "external_message_id, direction, sender_type, content, status, sequence, "
                "occurred_at) "
                "VALUES (:id, :t, :conn, :c, :ext, 'inbound', 'customer', 'Shop ơi?', 'received', "
                "1, :at)"
            ),
            {
                "id": uuid4(),
                "t": ctx.tenant_id,
                "conn": conn_id,
                "c": conv,
                "ext": f"in:{uuid4()}",
                "at": customer_at,
            },
        )
        seq = 1
        for i in range(prior_outbound_count):
            seq += 1
            await s.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                    "external_message_id, direction, sender_type, content, status, sequence, "
                    "occurred_at) "
                    "VALUES (:id, :t, :conn, :c, :ext, 'outbound', 'system', :txt, 'sent', :s, "
                    "now())"
                ),
                {
                    "id": uuid4(),
                    "t": ctx.tenant_id,
                    "conn": conn_id,
                    "c": conv,
                    "ext": f"sys:{i}:{uuid4()}",
                    "txt": f"Auto {i}",
                    "s": seq,
                },
            )
        await s.execute(
            text(
                "UPDATE conversations SET last_sequence = :seq, handling = 'bot' "
                "WHERE tenant_id = :t AND id = :c"
            ),
            {"seq": seq, "t": ctx.tenant_id, "c": conv},
        )
    return ctx, conv


async def test_messenger_blocked_until_a_delivery_adapter_exists(session_factory):
    now = datetime.now(UTC)

    # Trong 24h ⇒ gửi thành công
    ctx_ok, conv_ok = await _setup_channel_conv(
        session_factory, provider="messenger", customer_at=now - timedelta(hours=2)
    )
    async with session_factory() as s, s.begin():
        ok = await send_ai_reply(
            s,
            tenant_id=ctx_ok.tenant_id,
            conversation_id=conv_ok,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop mở cửa 8h-21h ạ.",
        )
    # Trong cửa sổ nhưng chưa có adapter giao tin cho kênh này ⇒ vẫn chặn (quy tắc cửa sổ được
    # kiểm ở test_autopilot_channels.py).
    assert ok.sent is False and ok.blocked_reason == "channel"

    # Quá 24h ⇒ chặn bằng `channel`
    ctx_old, conv_old = await _setup_channel_conv(
        session_factory, provider="messenger", customer_at=now - timedelta(hours=25)
    )
    async with session_factory() as s, s.begin():
        blocked = await send_ai_reply(
            s,
            tenant_id=ctx_old.tenant_id,
            conversation_id=conv_old,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop mở cửa 8h-21h ạ.",
        )
    assert blocked.sent is False and blocked.blocked_reason == "channel"


async def test_zalo_oa_blocked_until_a_delivery_adapter_exists(session_factory):
    now = datetime.now(UTC)

    # Trong 48h & dưới 8 tin phản hồi ⇒ gửi thành công
    ctx_ok, conv_ok = await _setup_channel_conv(
        session_factory, provider="zalo_oa", customer_at=now - timedelta(hours=30)
    )
    async with session_factory() as s, s.begin():
        ok = await send_ai_reply(
            s,
            tenant_id=ctx_ok.tenant_id,
            conversation_id=conv_ok,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop hỗ trợ đổi trong 7 ngày ạ.",
        )
    # Trong cửa sổ nhưng chưa có adapter giao tin cho kênh này ⇒ vẫn chặn (quy tắc cửa sổ được
    # kiểm ở test_autopilot_channels.py).
    assert ok.sent is False and ok.blocked_reason == "channel"

    # Quá 48h ⇒ chặn bằng `channel`
    ctx_old, conv_old = await _setup_channel_conv(
        session_factory, provider="zalo_oa", customer_at=now - timedelta(hours=49)
    )
    async with session_factory() as s, s.begin():
        exp = await send_ai_reply(
            s,
            tenant_id=ctx_old.tenant_id,
            conversation_id=conv_old,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop hỗ trợ đổi trong 7 ngày ạ.",
        )
    assert exp.sent is False and exp.blocked_reason == "channel"

    # Đủ 8 tin phản hồi liên tiếp sau tin khách ⇒ chặn bằng `channel`
    ctx_lim, conv_lim = await _setup_channel_conv(
        session_factory,
        provider="zalo_oa",
        customer_at=now - timedelta(hours=1),
        prior_outbound_count=8,
    )
    async with session_factory() as s, s.begin():
        lim = await send_ai_reply(
            s,
            tenant_id=ctx_lim.tenant_id,
            conversation_id=conv_lim,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Tin thứ 9",
        )
    assert lim.sent is False and lim.blocked_reason == "channel"

    # Hết hạn mức OA trên cấu hình kết nối ⇒ chặn bằng `channel`
    ctx_q, conv_q = await _setup_channel_conv(
        session_factory,
        provider="zalo_oa",
        customer_at=now - timedelta(minutes=10),
        config={"quota_exhausted": True},
    )
    async with session_factory() as s, s.begin():
        quota = await send_ai_reply(
            s,
            tenant_id=ctx_q.tenant_id,
            conversation_id=conv_q,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Hết hạn mức",
        )
    assert quota.sent is False and quota.blocked_reason == "channel"


async def test_price_quote_enables_when_ready_and_checks_catalog_freshness(
    new_client, session_factory
):
    # 1. API cho phép bật `price_quote` khi đủ 30 mẫu mù đạt ngưỡng
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    await _seed_agent(session_factory, ctx, conn, owner_id)
    cfg = {
        "template": "faq_sales_quote",
        "template_version": 5,
        "prompt_version": PROMPT_VERSION,
        "knowledge_hash": "k1",
        "model": "claude-sonnet-5",
    }
    now = datetime.now(UTC)
    async with session_factory() as s, s.begin():
        cust = await make_customer(s, ctx)
        conv = await make_conversation(s, ctx, cust, conn)
        for _ in range(30):
            await add(
                s,
                ctx,
                conv,
                intent="price_quote",
                config=cfg,
                sendable="as_is",
                label="sendable",
                when=now,
            )
    en = await owner.post("/api/v1/autopilot/intents/price_quote/enable", headers=CSRF)
    assert en.status_code == 200, en.text
    assert en.json()["state"] == "autopilot"

    # 2. AutopilotStage gửi `price_quote` khi catalog còn mới và chặn `catalog_stale` khi đã cũ
    w = await _setup_learning_shop(session_factory, enable_faq=False, auto_send=True)
    tpl = get_template("faq_sales_quote")
    product_id: UUID = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO autopilot_intents "
                "(tenant_id, agent_id, intent, state, config_key, source) "
                "VALUES (:t, :a, 'price_quote', 'autopilot', CAST(:cfg AS jsonb), 'blind')"
            ),
            {
                "t": w.ctx.tenant_id,
                "a": w.agent_id,
                "cfg": json.dumps(
                    {
                        "template": tpl.key,
                        "template_version": tpl.version,
                        "prompt_version": PROMPT_VERSION,
                        "models": primary_models(load_routes(), tpl),
                    }
                ),
            },
        )
        await s.execute(
            text(
                "INSERT INTO catalog_items "
                "(id, tenant_id, source, external_id, sku, name, price_vnd, variants, "
                " search_text, active, synced_at) "
                "VALUES (:id, :t, 'sheets', 'AK-01', 'AK-01', 'Áo khoác gió', 320000, '[]'::jsonb, "
                " 'ao khoac gio ak-01', true, now() - interval '30 hours')"
            ),
            {"id": product_id, "t": w.ctx.tenant_id},
        )
        # Đặt FAQ chứa đúng giá 320.000đ để validate_draft trong runner chấp nhận nháp mà không
        # cần gọi tool, đồng thời nháp trích nguồn sản phẩm catalog đang cũ (> 24h).
        await s.execute(
            text("UPDATE faq_entries SET answer = 'Áo khoác gió 320.000đ' WHERE id = :id"),
            {"id": w.faq_id},
        )

    stale_draft = draft_out(
        "Dạ áo khoác gió giá 320.000đ ạ.",
        sources=[("product", str(product_id))],
        needs_human=False,
        reason="",
    )
    # Lưu nháp `grounded=true` trực tiếp với nguồn product để kiểm tra đúng cổng `catalog_stale`.
    runner_stale, _ = make_runner(session_factory, [classify("price_quote"), stale_draft])
    stage_stale = AutopilotStage(session_factory, runner_stale.handle)
    req_stale = await request_for(session_factory, w, texts=("Áo khoác gió bao nhiêu?",))
    await runner_stale.handle(req_stale)
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE agent_drafts SET grounded = true "
                "WHERE tenant_id = :t AND conversation_id = :c"
            ),
            {"t": w.ctx.tenant_id, "c": req_stale.conversation_id},
        )
        await s.execute(
            text(
                "UPDATE agent_run_steps SET detail = '{\"problems\": []}'::jsonb "
                "WHERE tenant_id = :t AND kind = 'validate' AND name = 'draft'"
            ),
            {"t": w.ctx.tenant_id},
        )
    res_stale = await stage_stale.try_send(req_stale)
    assert res_stale == "catalog_stale"

    # Đồng bộ mới sản phẩm (`synced_at = now()`) ⇒ cổng gửi thành công!
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE catalog_items SET synced_at = now() WHERE tenant_id = :t AND id = :id"),
            {"t": w.ctx.tenant_id, "id": product_id},
        )
    res_fresh = await stage_stale.try_send(req_stale)
    assert res_fresh == "sent"


async def test_owner_uploaded_csv_catalog_never_goes_stale(session_factory):
    w = await _setup_learning_shop(session_factory, enable_faq=True, auto_send=True)
    product_id = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO catalog_items (id, tenant_id, source, external_id, sku, name, "
                "price_vnd, search_text, synced_at) VALUES (:id, :t, 'csv', 'Q-1', 'Q-1', "
                "'Quần jean', 420000, 'quan jean', now() - interval '30 days')"
            ),
            {"id": product_id, "t": w.ctx.tenant_id},
        )
        stale = await _check_catalog_stale(
            s,
            w.ctx.tenant_id,
            reply_text="Quần jean giá 420.000đ ạ.",
            sources=[{"type": "product", "id": str(product_id)}],
            max_age_hours=24,
        )
    assert stale is False
