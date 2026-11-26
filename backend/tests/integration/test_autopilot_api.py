"""API quản lý Autopilot theo ý định và nút dừng khẩn cấp (thiết kế 06 A2)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.modules.agent_management.infrastructure.repository import insert_agent
from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.llm.routes import load_routes
from app.modules.agent_runtime.prompts import PROMPT_VERSION
from app.modules.conversation_policy.domain.policy import suggested_config
from app.modules.conversation_policy.infrastructure.repository import publish, set_auto_send
from app.modules.outbound.domain.autopilot import primary_models

from .factories import make_conversation, make_customer
from .test_shadow_summary import add
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def _seed_agent(session_factory, ctx, conn, user_id):
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
        await publish(s, ctx.tenant_id, suggested_config(), created_by=user_id)
        await set_auto_send(s, ctx.tenant_id, True)
    return agent_id


async def test_autopilot_enable_disable_pause_and_permissions(new_client, session_factory):
    owner, ctx, owner_id, conn = await logged_in(new_client, session_factory, role="owner")
    await _seed_agent(session_factory, ctx, conn, owner_id)

    # 1. Admin và agent không có quyền autopilot.manage (chỉ owner)
    admin, *_ = await logged_in(new_client, session_factory, role="admin", ctx=ctx)
    assert (
        await admin.post("/api/v1/autopilot/intents/faq/enable", headers=CSRF)
    ).status_code == 403
    assert (await admin.post("/api/v1/autopilot/pause", headers=CSRF)).status_code == 403
    assert (await admin.get("/api/v1/autopilot/intents")).status_code == 200

    # 2. Chưa đủ mẫu / cấm bật ⇒ 422
    r_not_ready = await owner.post("/api/v1/autopilot/intents/faq/enable", headers=CSRF)
    assert r_not_ready.status_code == 422
    for forbidden in ("complaint", "order_intent", "other"):
        assert (
            await owner.post(f"/api/v1/autopilot/intents/{forbidden}/enable", headers=CSRF)
        ).status_code == 422

    # 3. Dựng đủ 30 cặp mù (27 as_is + 3 rewrite) + 30 nhãn trùng để hiệu chỉnh giám khảo
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
        for _ in range(27):
            await add(
                s, ctx, conv, intent="faq", config=cfg, sendable="as_is", label="sendable", when=now
            )
        for _ in range(3):
            await add(
                s,
                ctx,
                conv,
                intent="faq",
                config=cfg,
                verdict="incomplete",
                sendable="rewrite",
                label="rewrite",
                when=now,
            )

    listed = (await owner.get("/api/v1/autopilot/intents")).json()
    faq_item = next(i for i in listed["items"] if i["intent"] == "faq")
    assert faq_item["can_enable"] is True
    assert faq_item["state"] == "learning"

    # 4. Bật thành công ⇒ state = autopilot, lưu config_key và ghi signal
    enabled = await owner.post("/api/v1/autopilot/intents/faq/enable", headers=CSRF)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "autopilot"
    assert enabled.json()["source"] == "blind"
    assert enabled.json()["config_key"] == {
        "template": "faq_sales_quote",
        "template_version": 5,
        "prompt_version": PROMPT_VERSION,
        "models": primary_models(load_routes(), get_template("faq_sales_quote", 5)),
    }

    # 5. Tắt ý định ⇒ state = learning + demote_reason
    disabled = await owner.post("/api/v1/autopilot/intents/faq/disable", headers=CSRF)
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "learning"
    assert disabled.json()["demote_reason"] == "manual_disable"

    # 6. Nút dừng khẩn cấp ⇒ auto_send = false ngay
    paused = await owner.post("/api/v1/autopilot/pause", headers=CSRF)
    assert paused.status_code == 200
    assert paused.json()["auto_send"] is False

    async with session_factory() as s:
        auto_send = (
            await s.execute(
                text("SELECT auto_send FROM tenant_policy WHERE tenant_id = :t"),
                {"t": ctx.tenant_id},
            )
        ).scalar_one()
        kinds = [
            r[0]
            for r in (
                await s.execute(
                    text(
                        "SELECT kind FROM learning_signals "
                        "WHERE tenant_id = :t AND kind LIKE 'autopilot_%' ORDER BY created_at"
                    ),
                    {"t": ctx.tenant_id},
                )
            ).all()
        ]
    assert auto_send is False
    assert kinds == ["autopilot_enabled", "autopilot_demoted", "autopilot_demoted"]
