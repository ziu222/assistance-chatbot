"""Bước 2.3: FAQ, tài liệu nhỏ, catalog có cấu trúc, tool read-only cho agent."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_runtime.tools import TOOL_SPECS, run_tool

from .test_staff_api import CSRF, logged_in

HEADER = "sku,name,description,price_vnd,size,color,stock\n"
CATALOG = (
    HEADER
    + "AK-02,Áo khoác gió,Chống nước nhẹ cho đi mưa,350000,M,đen,5\n"
    + "AK-02,Áo khoác gió,Chống nước nhẹ cho đi mưa,350000,L,đen,0\n"
    + "Q-01,Quần jean nữ,Ống suông,420000,28,xanh,12\n"
    + "D-07,Đầm dạ hội,Dài,890000,S,đỏ,2\n"
)


@pytest.fixture
def new_client(app_client):
    return lambda **over: app_client("https://api.test", **over)


async def import_csv(client, body: str):
    return await client.post(
        "/api/v1/catalog/import",
        content=body.encode("utf-8"),
        headers=CSRF | {"Content-Type": "text/csv; charset=utf-8"},
    )


async def seeded(new_client, session_factory, role="owner"):
    client, ctx, uid, conn = await logged_in(new_client, session_factory, role="owner")
    r = await import_csv(client, CATALOG)
    assert r.status_code == 200, r.text
    return client, ctx


# ---------- import ----------


async def test_import_then_reimport_updates(new_client, session_factory):
    client, ctx, _, _ = await logged_in(new_client, session_factory, role="owner")
    first = await import_csv(client, CATALOG)
    assert first.json() == {"items": 3, "created": 3, "updated": 0}

    changed = CATALOG.replace("420000", "399000")
    second = await import_csv(client, changed)
    assert second.json() == {"items": 3, "created": 0, "updated": 3}
    async with session_factory() as s:
        price = (
            await s.execute(text("SELECT price_vnd FROM catalog_items WHERE sku = 'Q-01'"))
        ).scalar_one()
    assert price == 399000


async def test_invalid_csv_is_rejected_atomically(new_client, session_factory):
    client, *_ = await logged_in(new_client, session_factory, role="owner")
    bad = CATALOG + "X-1,Áo lỗi,,không-phải-số,,,1\n"
    r = await import_csv(client, bad)
    assert r.status_code == 422
    assert any("price_vnd" in e for e in r.json()["error"]["details"])
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM catalog_items"))).scalar_one() == 0


async def test_import_guards(new_client, session_factory):
    client, *_ = await logged_in(new_client, session_factory, role="owner")
    too_big = HEADER + "A,B,,1,,,1\n" * 200_000  # > 2 MB
    assert (await import_csv(client, too_big)).status_code == 413
    r = await client.post(
        "/api/v1/catalog/import", content=b"{}", headers=CSRF | {"Content-Type": "application/json"}
    )
    assert r.status_code == 415
    agent_client, *_ = await logged_in(new_client, session_factory, role="agent")
    assert (await import_csv(agent_client, CATALOG)).status_code == 403


# ---------- tìm kiếm + tool ----------


@pytest.mark.parametrize("query", ["áo khoác gió", "ao khoac gio", "khoac gio", "AO KHOAC"])
async def test_search_without_diacritics(new_client, session_factory, query):
    client, ctx = await seeded(new_client, session_factory)
    r = await client.get("/api/v1/catalog/search", params={"q": query})
    assert r.status_code == 200
    assert r.json()["items"][0]["sku"] == "AK-02"


async def test_search_products_tool_projection_and_tenant_scope(new_client, session_factory):
    _, ctx_a = await seeded(new_client, session_factory)
    _, ctx_b = await seeded(new_client, session_factory)  # tenant B có catalog giống hệt

    async with session_factory() as s:
        result = await run_tool(s, ctx_a.tenant_id, "search_products", {"query": "dam da hoi"})
        ids_a = {
            r[0]
            for r in await s.execute(
                text("SELECT id FROM catalog_items WHERE tenant_id = :t"), {"t": ctx_a.tenant_id}
            )
        }
    assert result.ok
    [item] = [i for i in result.data if i["sku"] == "D-07"]
    assert set(item) == {"id", "sku", "name", "price_vnd", "variants"}  # đã chiếu, không đổ nguyên
    assert item["price_vnd"] == 890000
    assert {i["id"] for i in result.data} <= {str(x) for x in ids_a}  # không lẫn của tenant B


async def test_search_respects_limit_and_active(new_client, session_factory):
    _, ctx = await seeded(new_client, session_factory)
    async with session_factory() as s:
        await s.execute(text("UPDATE catalog_items SET active = false WHERE sku = 'AK-02'"))
        await s.commit()
        result = await run_tool(
            s, ctx.tenant_id, "search_products", {"query": "ao khoac", "limit": 5}
        )
    assert all(i["sku"] != "AK-02" for i in result.data)


@pytest.mark.parametrize(
    "args",
    [
        {"query": "áo", "limit": 99},
        {"query": ""},
        {"query": "x" * 101},
        {"query": "áo", "tenant_id": str(uuid4())},  # model không được tự chọn tenant
        {},
    ],
)
async def test_tool_rejects_invalid_arguments(new_client, session_factory, args):
    _, ctx = await seeded(new_client, session_factory)
    async with session_factory() as s:
        result = await run_tool(s, ctx.tenant_id, "search_products", args)
    assert not result.ok and result.error == "invalid_arguments"


async def test_get_product_scoped_and_truncated(new_client, session_factory):
    _, ctx_a = await seeded(new_client, session_factory)
    _, ctx_b = await seeded(new_client, session_factory)
    async with session_factory() as s:
        await s.execute(
            text("UPDATE catalog_items SET description = repeat('x', 900) WHERE sku = 'Q-01'")
        )
        await s.commit()
        id_a = (
            await s.execute(
                text("SELECT id FROM catalog_items WHERE tenant_id = :t AND sku = 'Q-01'"),
                {"t": ctx_a.tenant_id},
            )
        ).scalar_one()
        mine = await run_tool(s, ctx_a.tenant_id, "get_product", {"product_id": str(id_a)})
        theirs = await run_tool(s, ctx_b.tenant_id, "get_product", {"product_id": str(id_a)})
        unknown = await run_tool(s, ctx_a.tenant_id, "nope", {})
    assert mine.ok and len(mine.data["description"]) == 500
    assert not theirs.ok and theirs.error == "not_found"  # ID đúng nhưng của tenant khác
    assert not unknown.ok and unknown.error == "unknown_tool"


def test_tool_specs_are_strict():
    names = [t["name"] for t in TOOL_SPECS]
    assert names == ["search_products", "get_product"]  # thứ tự cố định (prompt caching)
    for spec in TOOL_SPECS:
        assert spec["strict"] is True
        assert spec["input_schema"]["additionalProperties"] is False
        assert "tenant_id" not in spec["input_schema"]["properties"]


# ---------- FAQ + tài liệu ----------


async def test_faq_crud_and_archive(new_client, session_factory):
    client, *_ = await logged_in(new_client, session_factory, role="owner")
    r = await client.post(
        "/api/v1/knowledge/faqs",
        json={"question": "Shop mở cửa mấy giờ?", "answer": "8h-21h hằng ngày."},
        headers=CSRF,
    )
    assert r.status_code == 201
    faq = r.json()
    assert faq["status"] == "approved"

    r = await client.patch(
        f"/api/v1/knowledge/faqs/{faq['id']}", json={"answer": "8h-22h."}, headers=CSRF
    )
    assert r.json()["answer"] == "8h-22h."

    assert (
        await client.delete(f"/api/v1/knowledge/faqs/{faq['id']}", headers=CSRF)
    ).status_code == 204
    items = (await client.get("/api/v1/knowledge/faqs")).json()["items"]
    assert [i["status"] for i in items] == ["archived"]  # lưu trữ, không xóa cứng (giữ provenance)


async def test_doc_limits_and_permissions(new_client, session_factory):
    client, *_ = await logged_in(new_client, session_factory, role="owner")
    too_long = await client.post(
        "/api/v1/knowledge/docs", json={"title": "Chính sách", "body": "x" * 20001}, headers=CSRF
    )
    assert too_long.status_code == 422
    ok = await client.post(
        "/api/v1/knowledge/docs",
        json={"title": "Đổi trả", "body": "Đổi trong 7 ngày nếu còn tem."},
        headers=CSRF,
    )
    assert ok.status_code == 201

    agent_client, *_ = await logged_in(new_client, session_factory, role="agent")
    assert (await agent_client.get("/api/v1/knowledge/docs")).status_code == 200
    denied = await agent_client.post(
        "/api/v1/knowledge/docs", json={"title": "x", "body": "y"}, headers=CSRF
    )
    assert denied.status_code == 403


async def test_knowledge_tenant_isolation(new_client, session_factory):
    client_a, *_ = await logged_in(new_client, session_factory, role="owner")
    faq = (
        await client_a.post(
            "/api/v1/knowledge/faqs", json={"question": "Q?", "answer": "A."}, headers=CSRF
        )
    ).json()
    client_b, *_ = await logged_in(new_client, session_factory, role="owner")
    assert (await client_b.get("/api/v1/knowledge/faqs")).json()["items"] == []
    r = await client_b.patch(
        f"/api/v1/knowledge/faqs/{faq['id']}", json={"answer": "hack"}, headers=CSRF
    )
    assert r.status_code == 404
    assert (
        await client_b.delete(f"/api/v1/knowledge/faqs/{faq['id']}", headers=CSRF)
    ).status_code == 404
