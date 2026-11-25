"""Bản phát hành tri thức có phiên bản và quay lại (thiết kế 05 L2, ADR-0038)."""

import pytest
from sqlalchemy import text

from app.modules.agent_runtime.context import build_context

from .factories import seed_conversation, seed_message
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def test_manual_edits_publish_releases_and_restore_switches_context(
    new_client, session_factory
):
    client, ctx, _, conn = await logged_in(new_client, session_factory, role="owner")
    conv_id = await seed_conversation(session_factory, ctx, conn)
    await seed_message(session_factory, ctx, conv_id, sequence=1, text="Phí ship bao nhiêu?")

    r1 = await client.post(
        "/api/v1/knowledge/faqs",
        json={"question": "Phí ship?", "answer": "30.000đ toàn quốc", "status": "approved"},
        headers=CSRF,
    )
    assert r1.status_code == 201, r1.text
    faq_id = r1.json()["id"]

    async with session_factory() as s:
        ctx_v1 = await build_context(s, ctx.tenant_id, conv_id, 1, 1)
    assert "30.000đ toàn quốc" in ctx_v1.knowledge_block

    r2 = await client.patch(
        f"/api/v1/knowledge/faqs/{faq_id}",
        json={"answer": "25.000đ nội thành"},
        headers=CSRF,
    )
    assert r2.status_code == 200, r2.text

    async with session_factory() as s:
        ctx_v2 = await build_context(s, ctx.tenant_id, conv_id, 1, 1)
    assert "25.000đ nội thành" in ctx_v2.knowledge_block
    assert ctx_v2.knowledge_hash != ctx_v1.knowledge_hash

    listed = await client.get("/api/v1/knowledge/releases")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [(i["number"], i["is_current"], i["reason"]) for i in items] == [
        (2, True, "manual_edit"),
        (1, False, "manual_edit"),
    ]
    assert items[0]["knowledge_hash"] == ctx_v2.knowledge_hash
    assert items[1]["knowledge_hash"] == ctx_v1.knowledge_hash

    restored = await client.post("/api/v1/knowledge/releases/1/restore", headers=CSRF)
    assert restored.status_code == 200
    assert restored.json()["number"] == 1
    assert restored.json()["is_current"] is True

    async with session_factory() as s:
        ctx_after = await build_context(s, ctx.tenant_id, conv_id, 1, 1)
        signals = (
            await s.execute(
                text(
                    "SELECT kind, payload FROM learning_signals "
                    "WHERE tenant_id = :t AND kind = 'release_published' ORDER BY created_at"
                ),
                {"t": ctx.tenant_id},
            )
        ).all()
    assert ctx_after.knowledge_hash == ctx_v1.knowledge_hash
    assert "30.000đ toàn quốc" in ctx_after.knowledge_block
    assert "25.000đ nội thành" not in ctx_after.knowledge_block
    assert len(signals) == 3
    for _, payload in signals:
        assert "30.000đ" not in str(payload) and "25.000đ" not in str(payload)


async def test_restore_guards_not_found_and_permissions(new_client, session_factory):
    owner, *_ = await logged_in(new_client, session_factory, role="owner")
    assert (
        await owner.post("/api/v1/knowledge/releases/99/restore", headers=CSRF)
    ).status_code == 404

    agent, *_ = await logged_in(new_client, session_factory, role="agent")
    assert (await agent.get("/api/v1/knowledge/releases")).status_code == 200
    assert (
        await agent.post("/api/v1/knowledge/releases/1/restore", headers=CSRF)
    ).status_code == 403
