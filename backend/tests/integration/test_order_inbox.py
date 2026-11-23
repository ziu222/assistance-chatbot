"""Nhân viên xử lý phiếu đặt hàng (thiết kế 03 O3): danh sách, sửa, xác nhận, hủy, hết hạn."""

import json

from sqlalchemy import text

from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv
from app.modules.knowledge.infrastructure.catalog import import_catalog
from app.modules.orders.domain.order_flow import OrderFlowConfig
from app.modules.orders.infrastructure.repository import (
    OrderRequest,
    current_flow,
    publish_flow,
    save_request,
)
from app.modules.orders.infrastructure.staff_actions import expire_and_purge

from .test_staff_api import CSRF, inbound, logged_in

CATALOG = (
    "sku,name,description,price_vnd,size,color,stock\n"
    "AK-02,Áo khoác gió,,350000,M,đen,5\n"
    "AK-02,Áo khoác gió,,350000,L,đen,0\n"
)
PHONE = "0912345678"


async def shop_with_request(app_client, session_factory, visitor="v1"):
    """Shop có quy trình đặt hàng và một phiếu đang thu thập (thiếu địa chỉ)."""
    client, ctx, uid, conn = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    conv = (
        await inbound(session_factory, ctx, conn, "em muốn đặt áo khoác gió", visitor)
    ).conversation_id
    async with session_factory() as s, s.begin():
        await import_catalog(s, ctx.tenant_id, parse_catalog_csv(CATALOG))
        item_id = (
            await s.execute(
                text("SELECT id FROM catalog_items WHERE tenant_id = :t"), {"t": ctx.tenant_id}
            )
        ).scalar_one()
        await publish_flow(s, ctx.tenant_id, OrderFlowConfig(), None)
        flow = await current_flow(s, ctx.tenant_id)
        req = OrderRequest(
            id=None,
            items=[
                {
                    "catalog_item_id": str(item_id),
                    "sku": "AK-02",
                    "name": "Áo khoác gió",
                    "unit_price_vnd": 350000,
                    "options": ["M đen"],
                    "needs_variant": False,
                    "variant": "M đen",
                    "quantity": 1,
                }
            ],
            customer={"name": "Lan", "phone": PHONE},
            unverified=["name"],
            missing=["address"],
        )
        await save_request(
            s, ctx.tenant_id, conv, flow_release_id=flow.id, channel="webchat", req=req
        )
    return client, ctx, conv, req.id


async def test_list_masks_phone_and_detail_shows_it_with_current_stock(app_client, session_factory):
    client, _, _, rid = await shop_with_request(app_client, session_factory)
    [item] = (await client.get("/api/v1/order-requests")).json()["items"]
    assert item["status"] == "collecting" and item["customer"]["phone"] == "0912…678"
    assert item["missing"] == ["address"] and item["subtotal_vnd"] == 350000
    detail = (await client.get(f"/api/v1/order-requests/{rid}")).json()
    assert detail["customer"]["phone"] == PHONE
    assert detail["items"][0]["stock_now"] == 5


async def test_edit_then_confirm_closes_the_ticket_and_records_a_signal(
    app_client, session_factory
):
    client, ctx, conv, rid = await shop_with_request(app_client, session_factory)
    r = await client.patch(
        f"/api/v1/order-requests/{rid}",
        json={"customer": {"address": "12 Lê Lợi, Q1", "name": "Lan Anh"}},
        headers=CSRF,
    )
    body = r.json()
    assert body["status"] == "ready" and body["missing"] == []
    assert body["unverified"] == [] and body["staff_edited"] == ["address", "name"]
    bad = await client.patch(
        f"/api/v1/order-requests/{rid}", json={"customer": {"phone": "123"}}, headers=CSRF
    )
    assert bad.status_code == 422

    async with session_factory() as s:
        handoff = (
            await s.execute(
                text(
                    "SELECT status, order_request_id FROM handoff_requests "
                    "WHERE conversation_id = :c"
                ),
                {"c": conv},
            )
        ).one()
    assert handoff == ("open", rid)  # vừa đủ thông tin ⇒ ticket đơn hàng

    assert (await client.post(f"/api/v1/order-requests/{rid}/confirm", headers=CSRF)).json()["ok"]
    again = await client.post(f"/api/v1/order-requests/{rid}/confirm", headers=CSRF)
    assert again.status_code == 409
    async with session_factory() as s:
        status = (
            await s.execute(
                text("SELECT status FROM handoff_requests WHERE conversation_id = :c"), {"c": conv}
            )
        ).scalar_one()
        [signal] = (
            await s.execute(
                text(
                    "SELECT payload FROM learning_signals "
                    "WHERE kind = 'order_confirmed' AND tenant_id = :t"
                ),
                {"t": ctx.tenant_id},
            )
        ).scalars()
    assert status == "resolved"
    assert signal["staff_edited"] == ["address", "name"] and signal["channel"] == "webchat"
    assert PHONE not in json.dumps(signal) and "Lê Lợi" not in json.dumps(
        signal, ensure_ascii=False
    )
    assert (await client.get("/api/v1/order-requests")).json()["items"] == []
    done = (await client.get("/api/v1/order-requests?status=confirmed")).json()["items"]
    assert [d["id"] for d in done] == [str(rid)]


async def test_cancel_and_tenant_isolation(app_client, session_factory):
    client, _, _, rid = await shop_with_request(app_client, session_factory)
    other, *_ = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    assert (await other.get(f"/api/v1/order-requests/{rid}")).status_code == 404
    assert (
        await other.post(f"/api/v1/order-requests/{rid}/cancel", headers=CSRF)
    ).status_code == 409
    assert (await client.post(f"/api/v1/order-requests/{rid}/cancel", headers=CSRF)).json()["ok"]
    assert (
        await client.patch(f"/api/v1/order-requests/{rid}", json={"quantity": 2}, headers=CSRF)
    ).status_code == 409


async def test_abandoned_requests_expire_and_personal_data_is_purged(app_client, session_factory):
    _, ctx, _, rid = await shop_with_request(app_client, session_factory)
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE order_requests SET updated_at = now() - interval '73 hours' WHERE id = :id"
            ),
            {"id": rid},
        )
        assert (await expire_and_purge(s))["expired"] >= 1
        await s.execute(
            text("UPDATE order_requests SET closed_at = now() - interval '31 days' WHERE id = :id"),
            {"id": rid},
        )
        await expire_and_purge(s)
        row = (
            await s.execute(
                text(
                    "SELECT status, customer_fields, pii_purged_at, items FROM order_requests "
                    "WHERE id = :id"
                ),
                {"id": rid},
            )
        ).one()
    assert row.status == "expired" and row.customer_fields == {} and row.pii_purged_at
    assert row.items[0]["sku"] == "AK-02"  # phần không phải dữ liệu cá nhân giữ để thống kê
