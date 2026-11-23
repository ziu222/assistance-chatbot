"""Nhân viên xử lý phiếu đặt hàng (thiết kế 03 §5.3–§5.4, giai đoạn O3) và việc định kỳ.

Xác nhận/hủy đóng ticket đi kèm trong cùng transaction. Danh sách che số điện thoại; chỉ trang
chi tiết mới trả đầy đủ. Phiếu quá hạn tự `expired`; dữ liệu cá nhân bị xóa theo thời hạn.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.conversation_policy.infrastructure.handoffs import record_signal, transition
from app.modules.orders.domain.order_flow import OrderFlowConfig, mask_phone, missing_fields
from app.modules.orders.infrastructure.repository import OrderRequest, save_request

log = logging.getLogger(__name__)

# ponytail: hằng số chung cho mọi shop; thành cấu hình của tenant khi có shop cần khác.
COLLECTING_EXPIRES_H = 72
PURGE_CLOSED_DAYS = 30  # phiếu hủy / quá hạn
PURGE_CONFIRMED_DAYS = 180
CUSTOMER_FIELDS = ("name", "phone", "address", "delivery_time", "note")
OPEN = ("collecting", "ready")

_SELECT = """
    SELECT o.id, o.conversation_id, o.status, o.channel, o.items, o.subtotal_vnd,
           o.customer_fields, o.unverified, o.missing_fields, o.staff_edited, o.opened_at,
           o.ready_at, o.closed_at, o.flow_release_id, cu.display_name
    FROM order_requests o
    JOIN conversations c ON c.tenant_id = o.tenant_id AND c.id = o.conversation_id
    LEFT JOIN customers cu ON cu.tenant_id = c.tenant_id AND cu.id = c.customer_id
"""


def _json(r: Any, *, full: bool) -> dict:
    customer = dict(r.customer_fields)
    if not full and "phone" in customer:
        customer["phone"] = mask_phone(customer["phone"])
    return {
        "id": str(r.id),
        "conversation_id": str(r.conversation_id),
        "status": r.status,
        "channel": r.channel,
        "customer_name": r.display_name,
        "items": [
            {k: i.get(k) for k in ("sku", "name", "variant", "quantity", "unit_price_vnd")}
            for i in r.items
        ],
        "subtotal_vnd": r.subtotal_vnd,  # tạm tính, chưa gồm phí ship
        "customer": customer,
        "unverified": list(r.unverified),
        "missing": list(r.missing_fields),
        "staff_edited": list(r.staff_edited),
        "opened_at": r.opened_at.isoformat(),
        "ready_at": r.ready_at.isoformat() if r.ready_at else None,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
    }


async def list_requests(s: AsyncSession, tenant_id: UUID, statuses: list[str]) -> list[dict]:
    # ponytail: tối đa 100 phiếu một trang; thêm cursor khi shop có hàng đợi dài hơn.
    rows = await s.execute(
        text(
            _SELECT + "WHERE o.tenant_id = :t AND o.status = ANY(:st) "
            "ORDER BY (o.status = 'ready') DESC, COALESCE(o.ready_at, o.opened_at) LIMIT 100"
        ),
        {"t": tenant_id, "st": statuses},
    )
    return [_json(r, full=False) for r in rows]


async def _row(s: AsyncSession, tenant_id: UUID, request_id: UUID, *, lock: bool = False):
    return (
        await s.execute(
            text(
                _SELECT
                + "WHERE o.tenant_id = :t AND o.id = :id"
                + (" FOR UPDATE OF o" if lock else "")
            ),
            {"t": tenant_id, "id": request_id},
        )
    ).first()


async def get_request(s: AsyncSession, tenant_id: UUID, request_id: UUID) -> dict | None:
    """Chi tiết đầy đủ, kèm tồn kho HIỆN TẠI để nhân viên biết món đã hết sau khi ghi phiếu."""
    r = await _row(s, tenant_id, request_id)
    if r is None:
        return None
    out = _json(r, full=True)
    for item, raw in zip(out["items"], r.items, strict=True):
        stock = (
            await s.execute(
                text(
                    "SELECT variants FROM catalog_items "
                    "WHERE tenant_id = :t AND id = CAST(:id AS uuid) AND active"
                ),
                {"t": tenant_id, "id": raw.get("catalog_item_id")},
            )
        ).scalar_one_or_none()
        label = item.get("variant")
        match = [
            v
            for v in stock or []
            if " ".join(str(x) for x in (v.get("size"), v.get("color")) if x) == label
        ]
        item["stock_now"] = match[0].get("stock") if match else (None if stock is None else 0)
    return out


async def _config(s: AsyncSession, tenant_id: UUID, release_id: UUID) -> OrderFlowConfig:
    raw = (
        await s.execute(
            text("SELECT config FROM order_flow_releases WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": release_id},
        )
    ).scalar_one()
    return OrderFlowConfig.model_validate(raw)


async def edit_request(
    s: AsyncSession,
    tenant_id: UUID,
    request_id: UUID,
    *,
    customer: dict[str, str | None],
    quantity: int | None,
    variant: str | None,
) -> dict | None:
    """Nhân viên sửa phiếu đang mở. Trường đã sửa thành "đã xác minh" và được ghi lại để học."""
    r = await _row(s, tenant_id, request_id, lock=True)
    if r is None or r.status not in OPEN:
        return None
    items = [dict(i) for i in r.items]
    edited = set(r.staff_edited)
    cust = dict(r.customer_fields)
    for f, value in customer.items():
        if value:
            cust[f] = value
        else:
            cust.pop(f, None)
        edited.add(f)
    if items and quantity is not None:
        items[0]["quantity"] = quantity
        edited.add("quantity")
    if items and variant is not None:
        items[0]["variant"] = variant
        edited.add("variant")
    cfg = await _config(s, tenant_id, r.flow_release_id)
    req = OrderRequest(
        id=r.id,
        status=r.status,
        items=items,
        customer=cust,
        unverified=sorted(set(r.unverified) - edited),
        missing=missing_fields(cfg.for_channel(r.channel)[0], items, cust),
    )
    await save_request(
        s,
        tenant_id,
        r.conversation_id,
        flow_release_id=r.flow_release_id,
        channel=r.channel,
        req=req,
    )
    await s.execute(
        text("UPDATE order_requests SET staff_edited = :e WHERE tenant_id = :t AND id = :id"),
        {"e": sorted(edited), "t": tenant_id, "id": r.id},
    )
    return await get_request(s, tenant_id, r.id)


async def close_request(
    s: AsyncSession, tenant_id: UUID, request_id: UUID, action: str, user_id: UUID
) -> bool:
    """`confirm` hoặc `cancel` một phiếu đang mở; đóng ticket `source='order'` đi kèm."""
    status = {"confirm": "confirmed", "cancel": "cancelled"}[action]
    r = (
        await s.execute(
            text(
                "UPDATE order_requests SET status = :st, closed_at = now(), updated_at = now(), "
                "confirmed_by = CASE WHEN :st = 'confirmed' THEN CAST(:u AS uuid) END "
                "WHERE tenant_id = :t AND id = :id AND status IN ('collecting','ready') "
                "RETURNING conversation_id, channel, staff_edited, unverified, missing_fields"
            ),
            {"st": status, "u": user_id, "t": tenant_id, "id": request_id},
        )
    ).first()
    if r is None:
        return False
    handoff_id = (
        await s.execute(
            text(
                "SELECT id FROM handoff_requests WHERE tenant_id = :t AND order_request_id = :o "
                "AND status IN ('open','claimed')"
            ),
            {"t": tenant_id, "o": request_id},
        )
    ).scalar_one_or_none()
    if handoff_id is not None:
        await transition(s, tenant_id, handoff_id, "resolve", user_id)
    await s.execute(
        text(
            "INSERT INTO outbox_events (tenant_id, aggregate_type, aggregate_id, event_type, "
            "payload) VALUES (:t, 'conversation', :c, 'order_request.updated', CAST(:p AS jsonb))"
        ),
        {
            "t": tenant_id,
            "c": r.conversation_id,
            "p": json.dumps(
                {
                    "v": 1,
                    "event_type": "order_request.updated",
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(r.conversation_id),
                    "order_request_id": str(request_id),
                    "status": status,
                }
            ),
        },
    )
    # Đã chiếu: tên trường, không giá trị (§4.3). Nguồn cho gợi ý chỉnh quy trình ở bước 4.
    await record_signal(
        s,
        tenant_id,
        r.conversation_id,
        f"order_{status}",
        {
            "order_request_id": str(request_id),
            "channel": r.channel,
            "staff_edited": list(r.staff_edited),
            "unverified_left": list(r.unverified),
            "missing_at_close": list(r.missing_fields),
        },
    )
    return True


async def expire_and_purge(s: AsyncSession) -> dict[str, int]:
    """Việc định kỳ cho mọi tenant: phiếu bỏ dở quá hạn ⇒ `expired`; xóa dữ liệu cá nhân quá hạn."""
    expired = (
        await s.execute(
            text(
                "UPDATE order_requests SET status = 'expired', closed_at = now(), "
                "updated_at = now() "
                "WHERE status = 'collecting' AND updated_at < now() - make_interval(hours => :h)"
            ),
            {"h": COLLECTING_EXPIRES_H},
        )
    ).rowcount
    purged = (
        await s.execute(
            text(
                "UPDATE order_requests SET customer_fields = '{}', unverified = '{}', "
                "pii_purged_at = now() WHERE pii_purged_at IS NULL AND ("
                "(status IN ('cancelled','expired') "
                "AND closed_at < now() - make_interval(days => :c)) "
                "OR (status = 'confirmed' AND closed_at < now() - make_interval(days => :k)))"
            ),
            {"c": PURGE_CLOSED_DAYS, "k": PURGE_CONFIRMED_DAYS},
        )
    ).rowcount
    return {"expired": expired, "purged": purged}


async def run_order_maintenance(
    factory: async_sessionmaker[AsyncSession], stop: asyncio.Event, tick_s: float = 600.0
) -> None:
    while not stop.is_set():
        try:
            async with factory() as s, s.begin():
                done = await expire_and_purge(s)
            if any(done.values()):
                log.info("order maintenance", extra=done)
        except Exception:
            log.exception("order maintenance tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=tick_s)
