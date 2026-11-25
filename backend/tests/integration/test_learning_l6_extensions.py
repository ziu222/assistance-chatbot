"""Ứng viên `brand_example` và `order_flow` (thiết kế 05 L6 & 03 O6)."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.authoring.infrastructure.repository import current_profile
from app.modules.learning.infrastructure.candidates import (
    build_brand_examples_once,
    build_order_flow_candidates_once,
)
from app.modules.orders.infrastructure.repository import current_flow
from app.shared.infrastructure.signals import record_signal

from .test_learning_drafts import send, shop
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def test_brand_example_generated_from_repeated_as_is_and_published_on_approve(
    app_client, session_factory
):
    w, conv1, client = await shop(app_client, session_factory, mode="learning")
    sample_text = "Dạ em cảm ơn anh/chị đã quan tâm áo khoác gió bên shop ạ!"

    # Tạo 3 lượt dùng nguyên văn (`as_is`) cùng câu
    async with session_factory() as s, s.begin():
        run1 = (
            await s.execute(
                text(
                    "SELECT agent_id, agent_version_id, prompt_version, knowledge_hash, intents "
                    "FROM agent_runs WHERE tenant_id = :t LIMIT 1"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).one()
        draft_ids = []
        for seq in (2, 3, 4):
            rid = uuid4()
            await s.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(id, tenant_id, agent_id, agent_version_id, conversation_id, from_sequence, "
                    " to_sequence, status, prompt_version, knowledge_hash, intents) "
                    "VALUES (:id, :t, :aid, :avid, :c, :seq, :seq, 'completed', :pv, :kh, :intents)"
                ),
                {
                    "id": rid,
                    "t": w.ctx.tenant_id,
                    "aid": run1.agent_id,
                    "avid": run1.agent_version_id,
                    "c": conv1,
                    "seq": seq,
                    "pv": run1.prompt_version,
                    "kh": run1.knowledge_hash,
                    "intents": run1.intents,
                },
            )
            did = uuid4()
            await s.execute(
                text(
                    "INSERT INTO agent_drafts "
                    "(id, tenant_id, run_id, conversation_id, based_on_sequence, text, sources, "
                    " grounded, needs_human) "
                    "VALUES (:id, :t, :r, :c, :seq, :txt, '[]', true, false)"
                ),
                {
                    "id": did,
                    "t": w.ctx.tenant_id,
                    "r": rid,
                    "c": conv1,
                    "seq": seq,
                    "txt": sample_text,
                },
            )
            draft_ids.append(str(did))

    for did in draft_ids:
        await send(client, conv1, sample_text, draft_id=did)

    assert await build_brand_examples_once(session_factory) == 1

    owner, *_ = await logged_in(
        lambda **over: app_client("https://api.test", **over),
        session_factory,
        role="owner",
        ctx=w.ctx,
    )
    items = (await owner.get("/api/v1/learning/candidates?status=pending")).json()["items"]
    brand_cand = next(i for i in items if i["kind"] == "brand_example")
    r = await owner.post(f"/api/v1/learning/candidates/{brand_cand['id']}/approve", headers=CSRF)
    assert r.status_code == 200
    assert r.json()["status"] == "published"

    async with session_factory() as s:
        prof = await current_profile(s, w.ctx.tenant_id)
    assert prof is not None
    assert sample_text in prof[1].samples


async def test_order_flow_candidate_generated_from_order_confirmed_and_published(
    new_client, session_factory
):
    owner, ctx, _, _ = await logged_in(new_client, session_factory, role="owner")
    async with session_factory() as s, s.begin():
        for _ in range(3):
            await record_signal(
                s,
                ctx.tenant_id,
                None,
                "order_confirmed",
                {
                    "order_request_id": str(uuid4()),
                    "channel": "webchat",
                    "staff_edited": ["delivery_time"],
                    "unverified_left": [],
                    "missing_at_close": [],
                },
            )

    assert await build_order_flow_candidates_once(session_factory) == 1

    items = (await owner.get("/api/v1/learning/candidates?status=pending")).json()["items"]
    of_cand = next(i for i in items if i["kind"] == "order_flow")
    assert of_cand["proposal"]["field"] == "delivery_time"

    r = await owner.post(f"/api/v1/learning/candidates/{of_cand['id']}/approve", headers=CSRF)
    assert r.status_code == 200
    assert r.json()["status"] == "published"

    async with session_factory() as s:
        flow = await current_flow(s, ctx.tenant_id)
    assert flow is not None
    assert "delivery_time" in flow.config.required_fields
    assert json.loads(flow.config.model_dump_json())["required_fields"]
