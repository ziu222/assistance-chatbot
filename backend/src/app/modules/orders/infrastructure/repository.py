"""Quy trình đặt hàng (phát hành có phiên bản) và phiếu đặt hàng: SQL thẳng, mọi câu lọc tenant.

Ghi phiếu, mở ticket và outbox nằm trong CÙNG transaction của người gọi. Payload outbox chỉ có id
và trạng thái: dữ liệu cá nhân không rời bảng `order_requests` (ADR-0035).
"""

import json
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation_policy.infrastructure.handoffs import open_handoff
from app.modules.orders.domain.order_flow import OrderFlowConfig


@dataclass(frozen=True)
class FlowRelease:
    id: UUID
    number: int
    config: OrderFlowConfig


async def current_flow(s: AsyncSession, tenant_id: UUID) -> FlowRelease | None:
    row = (
        await s.execute(
            text(
                "SELECT r.id, r.number, r.config FROM tenant_order_flow p "
                "JOIN order_flow_releases r ON r.tenant_id = p.tenant_id "
                "AND r.id = p.current_release_id WHERE p.tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    return (
        FlowRelease(row.id, row.number, OrderFlowConfig.model_validate(row.config)) if row else None
    )


async def _point_to(s: AsyncSession, tenant_id: UUID, release_id: UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO tenant_order_flow (tenant_id, current_release_id) VALUES (:t, :r) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "current_release_id = EXCLUDED.current_release_id, "
            "updated_at = now()"
        ),
        {"t": tenant_id, "r": release_id},
    )


async def publish_flow(
    s: AsyncSession, tenant_id: UUID, config: OrderFlowConfig, user_id: UUID | None
) -> int:
    # Khóa theo tenant để hai lần phát hành song song không trùng số.
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"flow:{tenant_id}"})
    number = (
        await s.execute(
            text(
                "SELECT COALESCE(max(number), 0) + 1 FROM order_flow_releases WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    release_id = uuid4()
    await s.execute(
        text(
            "INSERT INTO order_flow_releases (id, tenant_id, number, config, created_by) "
            "VALUES (:id, :t, :n, CAST(:c AS jsonb), :u)"
        ),
        {
            "id": release_id,
            "t": tenant_id,
            "n": number,
            "c": config.model_dump_json(),
            "u": user_id,
        },
    )
    await _point_to(s, tenant_id, release_id)
    return number


async def restore_flow(s: AsyncSession, tenant_id: UUID, number: int) -> bool:
    release_id = (
        await s.execute(
            text("SELECT id FROM order_flow_releases WHERE tenant_id = :t AND number = :n"),
            {"t": tenant_id, "n": number},
        )
    ).scalar_one_or_none()
    if release_id is None:
        return False
    await _point_to(s, tenant_id, release_id)
    return True


async def list_releases(s: AsyncSession, tenant_id: UUID) -> list[dict]:
    rows = await s.execute(
        text(
            "SELECT number, created_at FROM order_flow_releases WHERE tenant_id = :t "
            "ORDER BY number DESC LIMIT 20"
        ),
        {"t": tenant_id},
    )
    return [{"number": r.number, "created_at": r.created_at.isoformat()} for r in rows]


@dataclass
class OrderRequest:
    id: UUID | None
    status: str = "collecting"
    items: list[dict] = field(default_factory=list)
    customer: dict = field(default_factory=dict)
    unverified: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    destination: str = "staff_confirms"  # gửi link ⇒ khách tự đặt, không mở ticket

    @property
    def worth_saving(self) -> bool:
        """Chưa có món nào lẫn thông tin nào (vd món khách hỏi đã hết) ⇒ không tạo phiếu rỗng."""
        return self.id is not None or bool(self.items or self.customer)


async def open_request(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> OrderRequest | None:
    row = (
        await s.execute(
            text(
                "SELECT id, status, items, customer_fields, unverified, missing_fields, "
                "destination "
                "FROM order_requests WHERE tenant_id = :t AND conversation_id = :c "
                "AND status IN ('collecting','ready')"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).first()
    if row is None:
        return None
    return OrderRequest(
        row.id,
        row.status,
        row.items,
        row.customer_fields,
        list(row.unverified),
        list(row.missing_fields),
        row.destination,
    )


async def save_request(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    flow_release_id: UUID,
    channel: str,
    req: OrderRequest,
) -> UUID:
    """Tạo hoặc cập nhật phiếu đang mở. Vừa đủ thông tin (`ready`) ⇒ mở ticket `source='order'`."""
    subtotal = sum(
        int(i.get("unit_price_vnd", 0)) * max(int(i.get("quantity") or 0), 1) for i in req.items
    )
    status = "ready" if not req.missing else "collecting"
    params = {
        "t": tenant_id,
        "c": conversation_id,
        "st": status,
        "items": json.dumps(req.items, ensure_ascii=False),
        "sub": subtotal,
        "cf": json.dumps(req.customer, ensure_ascii=False),
        "unv": req.unverified,
        "miss": req.missing,
        "dest": req.destination,
    }
    became_ready = False
    if req.id is None:
        req.id = uuid4()
        await s.execute(
            text(
                "INSERT INTO order_requests (id, tenant_id, conversation_id, status, "
                "flow_release_id, channel, items, subtotal_vnd, customer_fields, unverified, "
                "missing_fields, destination, ready_at) VALUES (:id, :t, :c, :st, :fr, :ch, "
                "CAST(:items AS jsonb), :sub, CAST(:cf AS jsonb), :unv, :miss, :dest, "
                "CASE WHEN :st = 'ready' THEN now() END)"
            ),
            params | {"id": req.id, "fr": flow_release_id, "ch": channel},
        )
        became_ready = status == "ready"
    else:
        previous = (
            await s.execute(
                text(
                    "UPDATE order_requests o SET status = :st, items = CAST(:items AS jsonb), "
                    "subtotal_vnd = :sub, customer_fields = CAST(:cf AS jsonb), unverified = :unv, "
                    "missing_fields = :miss, destination = :dest, updated_at = now(), "
                    "ready_at = CASE WHEN :st = 'ready' THEN COALESCE(o.ready_at, now()) END "
                    "FROM order_requests old "
                    "WHERE old.id = o.id AND o.tenant_id = :t AND o.id = :id "
                    "RETURNING old.status"
                ),
                params | {"id": req.id},
            )
        ).scalar_one()
        became_ready = status == "ready" and previous != "ready"
    req.status = status
    await s.execute(
        text(
            "INSERT INTO outbox_events "
            "(tenant_id, aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (:t, 'conversation', :c, 'order_request.updated', CAST(:p AS jsonb))"
        ),
        {
            "t": tenant_id,
            "c": conversation_id,
            "p": json.dumps(
                {
                    "v": 1,
                    "event_type": "order_request.updated",
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(conversation_id),
                    "order_request_id": str(req.id),
                    "status": status,
                }
            ),
        },
    )
    if became_ready and req.destination == "staff_confirms":
        handoff_id, created = await open_handoff(
            s, tenant_id, conversation_id, source="order", reason="order"
        )
        if created:
            await s.execute(
                text(
                    "UPDATE handoff_requests SET order_request_id = :o "
                    "WHERE tenant_id = :t AND id = :h"
                ),
                {"o": req.id, "t": tenant_id, "h": handoff_id},
            )
    return req.id
