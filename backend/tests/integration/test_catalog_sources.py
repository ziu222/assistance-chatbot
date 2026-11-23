"""Bước 2.13 (§13.2): nguồn catalog dạng adapter, sync idempotent, Google Sheets, độ tươi."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app.modules.agent_runtime.tools import run_tool
from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv
from app.modules.knowledge.infrastructure.catalog import import_catalog, sync_catalog
from app.modules.knowledge.infrastructure.catalog_sync import (
    market_items,
    save_source_config,
    sync_sheets,
)

from .factories import make_tenant
from .test_staff_api import CSRF, logged_in

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
HEADER = ["sku", "name", "description", "price_vnd", "size", "color", "stock"]
ROWS = [
    HEADER,
    ["SH-1", "Ấm trà gốm", "Gốm Bát Tràng", "180000", "", "trắng", "4"],
    ["SH-2", "Chén trà", "", "40000", "", "", "10"],  # ô cuối dòng có thể bị lược
]


def listing(item_id: str, sku: str, price: int, title: str = "🔥HOT🔥 Bình giữ nhiệt") -> dict:
    return {
        "item_id": item_id,
        "seller_sku": sku,
        "title": title,
        "description": "#giadung",
        "price_original": price * 2,
        "price_sale": price,
        "models": [{"name": "đen,1L", "stock": 3}],
    }


async def tenant(session_factory):
    async with session_factory() as s, s.begin():
        return (await make_tenant(s)).tenant_id


async def items(session_factory, t):
    async with session_factory() as s:
        rows = await s.execute(
            text(
                "SELECT sku, source, price_vnd, active, search_text FROM catalog_items "
                "WHERE tenant_id = :t ORDER BY sku"
            ),
            {"t": t},
        )
        return [tuple(r) for r in rows]


async def test_market_sync_is_idempotent_and_hides_removed_products(session_factory):
    t = await tenant(session_factory)
    batch, errors = market_items(
        [listing("1", "BX-1", 279000), listing("2", "BX-2", 150000), {"item_id": "3"}]
    )
    assert len(errors) == 1
    async with session_factory() as s, s.begin():
        first = await sync_catalog(s, t, "shopee", batch, full=True, errors=errors)
    # Có dòng lỗi ⇒ không ẩn gì (dòng lỗi không có nghĩa là sản phẩm đã bị gỡ).
    assert (first.created, first.deactivated) == (2, 0)
    async with session_factory() as s, s.begin():
        again = await sync_catalog(s, t, "shopee", batch[:1], full=True)
    assert (again.created, again.updated, again.deactivated) == (0, 1, 1)
    assert await items(session_factory, t) == [
        ("BX-1", "shopee", 279000, True, "hot binh giu nhiet"),
        ("BX-2", "shopee", 150000, False, "hot binh giu nhiet"),
    ]


async def test_one_source_owns_each_sku(session_factory):
    t = await tenant(session_factory)
    csv_body = "sku,name,description,price_vnd,size,color,stock\nBX-1,Bình,,100000,,,1\n"
    async with session_factory() as s, s.begin():
        await import_catalog(s, t, parse_catalog_csv(csv_body))
    batch, _ = market_items([listing("1", "BX-1", 279000)])
    async with session_factory() as s, s.begin():
        result = await sync_catalog(s, t, "shopee", batch, full=True)
    assert result.errors == ["sku BX-1 belongs to source csv"] and result.created == 0
    assert await items(session_factory, t) == [("BX-1", "csv", 100000, True, "binh")]


def fake_google(key_pem: bytes, values: list[list[str]], status: int = 200):
    """Phản hồi ghi sẵn của Google; kiểm tra assertion được ký bằng đúng khóa service account."""
    public = serialization.load_pem_private_key(key_pem, None).public_key()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "oauth2.googleapis.com":
            assertion = parse_qs(request.content.decode())["assertion"][0]
            claims = jwt.decode(assertion, public, algorithms=["RS256"], audience=str(request.url))
            assert claims["scope"].endswith("spreadsheets.readonly")
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        assert SHEET_ID in request.url.path
        return httpx.Response(status, json={"values": values})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def service_account() -> tuple[str, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    creds = {"client_email": "atlas@test.iam.gserviceaccount.com", "private_key": pem.decode()}
    return json.dumps(creds), pem


async def test_sheets_sync_reads_with_service_account_and_records_runs(session_factory):
    t = await tenant(session_factory)
    creds, pem = service_account()
    status, _ = await sync_sheets(session_factory, t, creds, httpx.AsyncClient())
    assert status == "failed"  # chưa cấu hình sheet: không gọi mạng

    async with session_factory() as s, s.begin():
        await save_source_config(s, t, "sheets", {"spreadsheet_id": SHEET_ID, "range": "A:G"})
    http, calls = fake_google(pem, ROWS)
    status, result = await sync_sheets(session_factory, t, creds, http)
    assert status == "ok" and result.created == 2
    assert calls == ["oauth2.googleapis.com", "sheets.googleapis.com"]

    http, _ = fake_google(pem, ROWS[:2])  # chủ shop xóa dòng SH-2 khỏi sheet
    status, result = await sync_sheets(session_factory, t, creds, http)
    assert (result.updated, result.deactivated) == (1, 1)

    http, _ = fake_google(pem, [], status=403)
    status, result = await sync_sheets(session_factory, t, creds, http)
    assert status == "failed" and result.errors == ["sheet_not_shared"]
    assert [i[3] for i in await items(session_factory, t)] == [True, False]  # không đụng dữ liệu

    async with session_factory() as s:
        runs = (
            await s.execute(
                text("SELECT status FROM catalog_sync_runs WHERE tenant_id = :t ORDER BY id"),
                {"t": t},
            )
        ).scalars()
        assert list(runs) == ["failed", "ok", "ok", "failed"]


async def test_tool_results_carry_synced_at_and_flag_stale_sources(session_factory):
    t = await tenant(session_factory)
    batch, _ = market_items([listing("1", "BX-1", 279000)])
    async with session_factory() as s, s.begin():
        await sync_catalog(s, t, "shopee", batch, full=True)
    async with session_factory() as s:
        fresh = await run_tool(s, t, "search_products", {"query": "binh giu nhiet"})
    assert fresh.data[0]["synced_at"] and not fresh.stale
    assert fresh.data[0]["price_vnd"] == 279000  # giá bán, không phải giá gốc

    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE catalog_items SET synced_at = now() - interval '2 days' "
                "WHERE tenant_id = :t"
            ),
            {"t": t},
        )
    async with session_factory() as s:
        assert (await run_tool(s, t, "search_products", {"query": "binh giu nhiet"})).stale


async def test_sources_api(app_client, session_factory):
    client, *_ = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    bad = await client.put(
        "/api/v1/catalog/sources/sheets",
        json={"spreadsheet_id": "../../evil"},
        headers=CSRF,
    )
    assert bad.status_code == 422
    ok = await client.put(
        "/api/v1/catalog/sources/sheets", json={"spreadsheet_id": SHEET_ID}, headers=CSRF
    )
    assert ok.json() == {"spreadsheet_id": SHEET_ID, "range": "A:G"}

    # Không có service account ⇒ lần sync thất bại rõ ràng, không gọi mạng.
    r = await client.post("/api/v1/catalog/sources/sheets/sync", headers=CSRF)
    assert r.json()["status"] == "failed"
    assert r.json()["errors"] == ["service_account_not_configured"]

    await client.post(
        "/api/v1/catalog/import",
        content=b"sku,name\n",
        headers=CSRF | {"Content-Type": "text/csv"},
    )
    body = (await client.get("/api/v1/catalog/sources")).json()
    assert body["sheets"] == {"spreadsheet_id": SHEET_ID, "range": "A:G"}
    assert [(r["source"], r["status"]) for r in body["runs"]] == [
        ("csv", "failed"),
        ("sheets", "failed"),
    ]
    assert datetime.fromisoformat(body["runs"][0]["finished_at"]) <= datetime.now(UTC)


async def test_scheduler_syncs_each_due_source_once_per_interval(session_factory):
    from datetime import timedelta

    from app.modules.knowledge.infrastructure.catalog_sync import sync_due_sources

    t1, t2 = await tenant(session_factory), await tenant(session_factory)
    async with session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM catalog_sources"))  # nguồn của test khác
        for t in (t1, t2):
            await save_source_config(s, t, "sheets", {"spreadsheet_id": SHEET_ID, "range": "A:G"})
    creds, pem = service_account()
    hour = timedelta(hours=1)

    http, calls = fake_google(pem, ROWS)
    assert await sync_due_sources(session_factory, creds, http, hour) == 2
    assert await sync_due_sources(session_factory, creds, http, hour) == 0  # chưa tới hạn
    assert len(calls) == 4

    # Lần thất bại cũng tính là đã thử: không gọi lại nguồn hỏng liên tục.
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE catalog_sync_runs SET finished_at = now() - interval '2 hours'")
        )
    http, _ = fake_google(pem, [], status=403)
    assert await sync_due_sources(session_factory, creds, http, hour) == 2
    assert await sync_due_sources(session_factory, creds, http, hour) == 0
