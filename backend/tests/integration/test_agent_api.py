"""Bước 2.2: tạo agent từ template, mode manual/shadow có kiểm tra version, kill switch, quyền."""

import json

import pytest
from sqlalchemy import text

from app.modules.agent_management.infrastructure.templates import get_template

from .factories import make_connection
from .test_staff_api import CSRF, logged_in


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def owner(new_client, session_factory, role="owner"):
    return await logged_in(new_client, session_factory, role=role)


async def create(client, conn_id, **over):
    body = {
        "template_key": "faq_sales_quote",
        "channel_connection_id": str(conn_id),
        "name": "Trợ lý bán hàng",
    } | over
    return await client.post("/api/v1/agents", json=body, headers=CSRF)


async def test_list_templates(new_client, session_factory):
    client, *_ = await owner(new_client, session_factory, role="agent")
    r = await client.get("/api/v1/agent-templates")
    assert r.status_code == 200
    versions = {x["version"]: x for x in r.json()["items"] if x["key"] == "faq_sales_quote"}
    assert {1, 2} <= set(versions)  # bản cũ vẫn liệt kê: agent cũ giữ ảnh chụp của nó
    assert all(
        t["default_mode"] == "shadow" and "complaint" in t["intents"] for t in versions.values()
    )


async def test_create_agent_snapshots_template_and_starts_manual(new_client, session_factory):
    client, ctx, uid, conn_id = await owner(new_client, session_factory)
    r = await create(client, conn_id)
    assert r.status_code == 201, r.text
    agent = r.json()
    assert agent["mode"] == "manual"  # an toàn: admin chủ động bật Shadow
    assert agent["version"] == 1
    assert agent["current_version"]["version_number"] == 1
    latest = get_template("faq_sales_quote").version  # không nêu version ⇒ bản mới nhất
    assert (agent["template_key"], agent["template_version"]) == ("faq_sales_quote", latest)

    async with session_factory() as s:
        config, created_by = (
            await s.execute(text("SELECT config, created_by FROM agent_versions"))
        ).one()
    expected = json.loads(get_template("faq_sales_quote", latest).model_dump_json())
    assert config == expected  # ảnh chụp, không phải tham chiếu tới file
    assert created_by == uid


async def test_older_template_version_can_still_be_chosen(new_client, session_factory):
    """Template v2 ra đời không đổi agent cũ, và vẫn tạo được agent từ v1 nếu cần so sánh."""
    client, _, _, conn_id = await owner(new_client, session_factory)
    r = await create(client, conn_id, template_version=1)
    assert r.status_code == 201 and r.json()["template_version"] == 1


async def test_one_agent_per_connection(new_client, session_factory):
    client, _, _, conn_id = await owner(new_client, session_factory)
    assert (await create(client, conn_id)).status_code == 201
    r = await create(client, conn_id, name="Trùng")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "agent_exists"


@pytest.mark.parametrize(
    "body",
    [
        {"template_key": "nope"},
        {"template_key": "faq_sales_quote", "template_version": 99},
        {"name": ""},
        {"mode": "shadow"},  # không cho đặt mode khi tạo
    ],
)
async def test_create_validation(new_client, session_factory, body):
    client, _, _, conn_id = await owner(new_client, session_factory)
    r = await create(client, conn_id, **body)
    assert r.status_code in (404, 422), r.text


async def test_create_on_other_tenant_connection_is_404(new_client, session_factory):
    client_a, *_ = await owner(new_client, session_factory)
    _, ctx_b, _, conn_b = await owner(new_client, session_factory)
    r = await create(client_a, conn_b)
    assert r.status_code == 404


async def test_agent_role_cannot_manage(new_client, session_factory):
    client, _, _, conn_id = await owner(new_client, session_factory, role="agent")
    r = await create(client, conn_id)
    assert r.status_code == 403
    assert (
        await client.patch("/api/v1/ai-settings", json={"ai_enabled": True}, headers=CSRF)
    ).status_code == 403
    assert (await client.get("/api/v1/agents")).status_code == 200  # đọc được


async def test_change_mode_with_optimistic_concurrency(new_client, session_factory):
    client, _, _, conn_id = await owner(new_client, session_factory)
    agent = (await create(client, conn_id)).json()
    url = f"/api/v1/agents/{agent['id']}"

    ok = await client.patch(url, json={"mode": "shadow", "version": 1}, headers=CSRF)
    assert ok.status_code == 200
    assert (ok.json()["mode"], ok.json()["version"]) == ("shadow", 2)

    stale = await client.patch(url, json={"mode": "manual", "version": 1}, headers=CSRF)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert (await client.get(url)).json()["mode"] == "shadow"  # không bị ghi đè


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "autopilot", "version": 1},  # bước 2 chưa có Autopilot
        {"shadow_sample_rate": 1.5, "version": 1},
        {"shadow_sample_rate": -0.1, "version": 1},
        {"mode": "shadow"},  # thiếu version
    ],
)
async def test_patch_validation(new_client, session_factory, body):
    client, _, _, conn_id = await owner(new_client, session_factory)
    agent = (await create(client, conn_id)).json()
    r = await client.patch(f"/api/v1/agents/{agent['id']}", json=body, headers=CSRF)
    assert r.status_code == 422


async def test_sample_rate_update(new_client, session_factory):
    client, _, _, conn_id = await owner(new_client, session_factory)
    agent = (await create(client, conn_id)).json()
    r = await client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"shadow_sample_rate": 0.25, "version": 1},
        headers=CSRF,
    )
    assert r.json()["shadow_sample_rate"] == 0.25


async def test_ai_settings_default_off_then_toggle(new_client, session_factory):
    client, *_ = await owner(new_client, session_factory)
    r = await client.get("/api/v1/ai-settings")
    assert r.json() == {"ai_enabled": False, "daily_cost_cap_micro": 2_000_000}

    r = await client.patch(
        "/api/v1/ai-settings",
        json={"ai_enabled": True, "daily_cost_cap_micro": 500_000},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json() == {"ai_enabled": True, "daily_cost_cap_micro": 500_000}
    assert (await client.get("/api/v1/ai-settings")).json()["ai_enabled"] is True

    bad = await client.patch("/api/v1/ai-settings", json={"daily_cost_cap_micro": -1}, headers=CSRF)
    assert bad.status_code == 422


async def test_tenant_isolation(new_client, session_factory):
    client_a, _, _, conn_a = await owner(new_client, session_factory)
    agent = (await create(client_a, conn_a)).json()
    await client_a.patch("/api/v1/ai-settings", json={"ai_enabled": True}, headers=CSRF)
    client_b, *_ = await owner(new_client, session_factory)

    assert (await client_b.get("/api/v1/agents")).json()["items"] == []
    assert (await client_b.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
    r = await client_b.patch(
        f"/api/v1/agents/{agent['id']}", json={"mode": "shadow", "version": 1}, headers=CSRF
    )
    assert r.status_code == 404
    assert (await client_b.get("/api/v1/ai-settings")).json()["ai_enabled"] is False


async def test_second_connection_can_have_its_own_agent(new_client, session_factory):
    client, ctx, _, conn_id = await owner(new_client, session_factory)
    async with session_factory() as s:
        conn2 = await make_connection(s, ctx)
        await s.commit()
    assert (await create(client, conn_id)).status_code == 201
    assert (await create(client, conn2, name="Kênh 2")).status_code == 201
    assert len((await client.get("/api/v1/agents")).json()["items"]) == 2
