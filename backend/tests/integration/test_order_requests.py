"""Luồng đặt hàng (thiết kế 03 O2, ADR-0035): quy trình theo shop, phiếu đặt hàng, ticket."""

import json

from sqlalchemy import text

from app.modules.agent_runtime.schemas import OrderDraftOut, OrderFields, Source
from app.modules.orders.domain.order_flow import OrderFlowConfig
from app.modules.orders.infrastructure.repository import publish_flow

from .test_agent_runtime import (
    classify,
    drafts,
    make_runner,
    make_world,
    product_from,
    request_for,
    result,
    search,
)
from .test_staff_api import CSRF, logged_in

PHONE = "0912345678"


def order_draft(reply: str, *, sku="", quantity=0, name="", address="", sources=()):
    return result(
        OrderDraftOut(
            reply=reply,
            sources=[Source(type="product", id=i) for i in sources],
            needs_human=False,  # model quên cũng không sao: code luôn đặt cần nhân viên
            reason="",
            order=OrderFields(
                sku=sku,
                variant="",
                quantity=quantity,
                name=name,
                address=address,
                delivery_time="",
                note="",
            ),
        )
    )


async def enable(session_factory, w, **over):
    async with session_factory() as s, s.begin():
        await publish_flow(s, w.tenant_id, OrderFlowConfig(**over), None)


async def order_rows(session_factory):
    async with session_factory() as s:
        return (await s.execute(text("SELECT * FROM order_requests"))).mappings().all()


async def test_shop_without_order_flow_gets_a_draft_not_an_instant_handoff(session_factory):
    w = await make_world(session_factory)
    runner, llm = make_runner(
        session_factory,
        [
            classify("order_intent"),
            search(),
            lambda req: result(
                __import__("app.modules.agent_runtime.schemas", fromlist=["DraftOut"]).DraftOut(
                    reply="Dạ áo khoác gió còn hàng, giá 350.000đ. Nhân viên sẽ hỗ trợ đặt ạ",
                    sources=[Source(type="product", id=product_from(req)["id"])],
                    needs_human=True,
                    reason="order",
                )
            ),
        ],
    )
    await runner.handle(await request_for(session_factory, w, ("em muốn đặt áo khoác gió",)))
    [draft] = await drafts(session_factory)
    assert draft["needs_human"] and draft["reason"] == "order" and draft["grounded"]
    assert await order_rows(session_factory) == []
    assert not llm.requests[-1].system_blocks[-1].startswith("<order_flow>")


async def test_order_request_is_collected_then_handed_to_staff(session_factory):
    w = await make_world(session_factory)
    await enable(session_factory, w, confirm_text="Shop gọi lại xác nhận trong 30 phút ạ")

    # Lượt 1: khách muốn đặt ⇒ tra kho, ghi phiếu, hỏi đúng một mục còn thiếu.
    runner, llm = make_runner(
        session_factory,
        [
            classify("order_intent"),
            search(),
            lambda req: order_draft(
                "Dạ áo khoác gió đen size M còn hàng, giá 350.000đ. Anh/chị lấy mấy cái ạ?",
                sku=product_from(req)["sku"],
                sources=[product_from(req)["id"]],
            ),
        ],
    )
    first = await request_for(session_factory, w, ("em muốn đặt áo khoác gió",))
    await runner.handle(first)
    [req] = await order_rows(session_factory)
    assert req["status"] == "collecting"
    assert list(req["missing_fields"]) == ["quantity", "name", "phone", "address"]
    [item] = req["items"]
    assert (item["sku"], item["variant"], item["unit_price_vnd"]) == ("AK-02", "M đen", 350000)
    block = llm.requests[-1].system_blocks[-1]
    assert block.startswith("<order_flow>") and "tra món khách nhắc tới" in block
    [draft] = await drafts(session_factory)
    assert not draft["needs_human"] and draft["grounded"]  # đang thu thập: bot tự hỏi tiếp

    # Lượt 2: khách gửi đủ thông tin. Sku bịa bị bỏ qua; số điện thoại do code tách.
    runner, llm = make_runner(
        session_factory,
        [
            classify("order_intent"),
            order_draft(
                "Shop gọi lại xác nhận trong 30 phút ạ",
                sku="XX-99",
                quantity=1,
                name="Lan",
                address="12 Lê Lợi, Q1",
            ),
        ],
    )
    await runner.handle(
        await request_for(session_factory, w, ("1 cái, tên Lan, sđt 0912 345 678, 12 Lê Lợi Q1",))
    )
    assert "Chỉ hỏi MỘT mục: số lượng" in llm.requests[-1].system_blocks[-1]
    [req] = await order_rows(session_factory)
    assert req["status"] == "ready" and list(req["missing_fields"]) == []
    assert req["items"][0]["sku"] == "AK-02" and req["subtotal_vnd"] == 350000
    assert req["customer_fields"]["phone"] == PHONE
    assert set(req["unverified"]) == {"name", "address"}  # phone do code tách: không nằm đây
    last = max(await drafts(session_factory), key=lambda d: d["created_at"])
    assert last["needs_human"] and last["reason"] == "order"  # đủ thông tin: cần nhân viên

    async with session_factory() as s:
        handoff = (
            await s.execute(
                text("SELECT source, order_request_id FROM handoff_requests WHERE tenant_id = :t"),
                {"t": w.tenant_id},
            )
        ).one()
        handling = (
            await s.execute(
                text("SELECT handling FROM conversations WHERE id = :c"),
                {"c": first.conversation_id},
            )
        ).scalar_one()
        payloads = (
            await s.execute(
                text(
                    "SELECT payload FROM outbox_events "
                    "WHERE event_type = 'order_request.updated' AND tenant_id = :t"
                ),
                {"t": w.tenant_id},
            )
        ).scalars()
        signals = (
            await s.execute(
                text("SELECT payload FROM learning_signals WHERE tenant_id = :t"),
                {"t": w.tenant_id},
            )
        ).scalars()
        leaked = [json.dumps(p) for p in [*payloads, *signals]]
    assert handoff == ("order", req["id"]) and handling == "waiting_human"
    assert leaked and not any(PHONE in p or "Lê Lợi" in p for p in leaked)


async def test_draft_claiming_the_order_is_placed_is_not_grounded(session_factory):
    w = await make_world(session_factory)
    await enable(session_factory, w)
    runner, _ = make_runner(
        session_factory,
        [classify("order_intent"), order_draft("Dạ em đã đặt hàng thành công cho anh/chị ạ")],
    )
    await runner.handle(await request_for(session_factory, w, ("chốt cho em 1 áo khoác gió",)))
    [draft] = await drafts(session_factory)
    assert draft["grounded"] is False


async def test_order_flow_api_publish_and_restore(app_client, session_factory):
    client, *_ = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    empty = (await client.get("/api/v1/order-flow")).json()
    assert empty["current"] is None and empty["config"]["enabled"] is False

    bad = await client.post(
        "/api/v1/order-flow/releases", json={"required_fields": ["email"]}, headers=CSRF
    )
    assert bad.status_code == 422
    r = await client.post(
        "/api/v1/order-flow/releases",
        json={"by_channel": {"webchat": {"destination": "product_link"}}},
        headers=CSRF,
    )
    assert r.json()["current"] == 1
    r = await client.post(
        "/api/v1/order-flow/releases", json={"payment_methods": ["bank_transfer"]}, headers=CSRF
    )
    assert r.json()["current"] == 2
    r = await client.post("/api/v1/order-flow/releases/1/restore", headers=CSRF)
    assert r.json()["current"] == 1
    assert r.json()["config"]["by_channel"]["webchat"]["destination"] == "product_link"
    assert (
        await client.post("/api/v1/order-flow/releases/9/restore", headers=CSRF)
    ).status_code == 404
