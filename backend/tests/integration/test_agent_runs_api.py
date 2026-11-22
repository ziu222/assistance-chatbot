"""Bước 2.6: API đọc run cho trace view, danh sách kênh và mở nguồn sản phẩm."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_management.infrastructure.repository import upsert_ai_settings
from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv
from app.modules.knowledge.infrastructure.catalog import import_catalog

from .test_agent_runtime import (
    CATALOG,
    World,
    classify,
    make_runner,
    quote_draft,
    request_for,
    search,
)
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def setup(new_client, session_factory, role="owner"):
    """Nhân viên đăng nhập + agent shadow + một run đã chạy xong bằng FakeLLM."""
    client, ctx, uid, conn = await logged_in(new_client, session_factory, role=role)
    created = await client.post(
        "/api/v1/agents",
        json={"template_key": "faq_sales_quote", "channel_connection_id": str(conn), "name": "Bot"},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    async with session_factory() as s, s.begin():
        await s.execute(text("UPDATE agents SET mode = 'shadow'"))
        await upsert_ai_settings(s, ctx.tenant_id, ai_enabled=True, daily_cost_cap_micro=None)
        await import_catalog(s, ctx.tenant_id, parse_catalog_csv(CATALOG))
    runner, _ = make_runner(session_factory, [classify("price_quote"), search(), quote_draft()])
    req = await request_for(session_factory, World(ctx, conn, uuid4(), uuid4()))
    await runner.handle(req)
    return client, ctx, conn, req


async def test_list_runs_and_filters(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)

    r = await client.get("/api/v1/agent-runs")
    assert r.status_code == 200
    [item] = r.json()["items"]
    assert (item["status"], item["intents"]) == ("completed", ["price_quote"])
    assert item["grounded"] is True and item["cost_micro"] > 0
    assert item["conversation_id"] == str(req.conversation_id)

    by_conv = await client.get(f"/api/v1/agent-runs?conversation_id={req.conversation_id}")
    assert len(by_conv.json()["items"]) == 1
    assert (await client.get("/api/v1/agent-runs?status=failed")).json()["items"] == []
    other = await client.get(f"/api/v1/agent-runs?conversation_id={uuid4()}")
    assert other.json()["items"] == []
    assert (await client.get("/api/v1/agent-runs?status=bogus")).status_code == 422


async def test_pagination_walks_every_run_once(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)
    async with session_factory() as s, s.begin():  # thêm 4 run cũ hơn bằng cách sao chép
        await s.execute(
            text(
                "INSERT INTO agent_runs (id, tenant_id, agent_id, agent_version_id, "
                "conversation_id, from_sequence, to_sequence, status, knowledge_hash, "
                "prompt_version, started_at) SELECT gen_random_uuid(), tenant_id, agent_id, "
                "agent_version_id, conversation_id, from_sequence, to_sequence, 'skipped', '', "
                "prompt_version, started_at - make_interval(secs => g) "
                "FROM agent_runs, generate_series(1, 4) g"
            )
        )
    async with session_factory() as s:
        total = (await s.execute(text("SELECT count(*) FROM agent_runs"))).scalar_one()

    seen, cursor = [], None
    while True:
        url = "/api/v1/agent-runs?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = (await client.get(url)).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == total
    assert (await client.get("/api/v1/agent-runs?cursor=zzz")).status_code == 422


async def test_run_detail_is_a_full_redacted_trace(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)
    [item] = (await client.get("/api/v1/agent-runs")).json()["items"]

    r = await client.get(f"/api/v1/agent-runs/{item['id']}")
    assert r.status_code == 200
    run = r.json()
    assert [m["content"] for m in run["input_messages"]] == ["áo khoác gió giá bao nhiêu"]
    assert [s["kind"] for s in run["steps"]][3:] == ["classify", "llm", "tool", "llm", "validate"]
    assert run["draft"]["grounded"] and run["draft"]["sources"][0]["type"] == "product"
    assert run["agent_version"]["number"] == 1
    assert len(run["knowledge_hash"]) == 64 and run["prompt_version"]
    assert [u["route"] for u in run["usage"]] == ["classify", "draft", "draft"]
    assert sum(u["cost_micro"] for u in run["usage"]) == run["totals"]["cost_micro"]
    # Không có prompt đầy đủ hay lời khách gõ trong tham số tool.
    assert "ao khoac gio" not in json.dumps([s["detail"] for s in run["steps"]])


async def test_run_detail_not_found_or_other_tenant(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)
    [item] = (await client.get("/api/v1/agent-runs")).json()["items"]
    other, *_ = await logged_in(new_client, session_factory, role="owner")

    assert (await other.get(f"/api/v1/agent-runs/{item['id']}")).status_code == 404
    assert (await other.get("/api/v1/agent-runs")).json()["items"] == []
    assert (await client.get(f"/api/v1/agent-runs/{uuid4()}")).status_code == 404


async def test_plain_agent_role_can_read_but_not_manage(new_client, session_factory):
    client, ctx, uid, conn = await logged_in(new_client, session_factory, role="agent")
    assert (await client.get("/api/v1/agent-runs")).status_code == 200
    assert (await client.get("/api/v1/channel-connections")).status_code == 200
    body = {"template_key": "faq_sales_quote", "channel_connection_id": str(conn), "name": "B"}
    assert (await client.post("/api/v1/agents", json=body, headers=CSRF)).status_code == 403


async def test_requires_login(new_client):
    client = await new_client()
    assert (await client.get("/api/v1/agent-runs")).status_code == 401
    assert (await client.get("/api/v1/channel-connections")).status_code == 401


async def test_channel_connections_show_which_have_an_agent(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)
    [c] = (await client.get("/api/v1/channel-connections")).json()["items"]
    assert c["id"] == str(conn) and c["provider"] == "webchat" and c["agent_id"]


async def test_catalog_item_for_source_link(new_client, session_factory):
    client, ctx, conn, req = await setup(new_client, session_factory)
    [p] = (await client.get("/api/v1/catalog/search?q=ao khoac")).json()["items"]

    r = await client.get(f"/api/v1/catalog/items/{p['id']}")
    assert r.status_code == 200 and r.json()["price_vnd"] == 350000
    assert (await client.get(f"/api/v1/catalog/items/{uuid4()}")).status_code == 404
