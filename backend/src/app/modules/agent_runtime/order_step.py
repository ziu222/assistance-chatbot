"""Bước đặt hàng trong một run (thiết kế 03 §5.2): nạp quy trình + phiếu, rồi cập nhật phiếu.

Code quyết định sản phẩm (chỉ sku có trong kết quả tool của run), giá (catalog), số điện thoại
(tách bằng code) và trường còn thiếu. Model chỉ góp biến thể, số lượng và vài trường chữ, và các
trường chữ đó bị gắn "chưa xác minh".
"""

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_runtime.schemas import OrderFields
from app.modules.knowledge.domain.text import fold
from app.modules.orders.domain.order_flow import (
    OrderFlowConfig,
    extract_phone,
    missing_fields,
    plan,
    render_block,
)
from app.modules.orders.infrastructure.repository import (
    OrderRequest,
    current_flow,
    open_request,
)

TEXT_FIELDS = ("name", "address", "delivery_time", "note")


@dataclass
class OrderContext:
    release_id: UUID
    config: OrderFlowConfig
    provider: str
    request: OrderRequest

    @property
    def required(self) -> list[str]:
        return plan(self.config, self.provider, self.request.items)[0]

    def block(self) -> str:
        r = self.request
        return render_block(self.config, self.provider, r.items, r.customer, r.missing)


async def load_order(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, *, order_intent: bool
) -> OrderContext | None:
    """None ⇒ không vào luồng đặt hàng: shop chưa bật, hoặc khách chưa muốn đặt và chưa có phiếu."""
    flow = await current_flow(s, tenant_id)
    if flow is None or not flow.config.enabled:
        return None
    req = await open_request(s, tenant_id, conversation_id)
    if req is None and not order_intent:
        return None
    provider = (
        await s.execute(
            text(
                "SELECT cc.provider FROM conversations c JOIN channel_connections cc "
                "ON cc.tenant_id = c.tenant_id AND cc.id = c.channel_connection_id "
                "WHERE c.tenant_id = :t AND c.id = :c"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).scalar_one()
    return OrderContext(flow.id, flow.config, provider, req or OrderRequest(id=None))


def with_phone(ctx: OrderContext, customer_texts: list[str]) -> None:
    """Trước khi soạn: số điện thoại khách vừa gõ (tách bằng code) và trường còn thiếu."""
    req = ctx.request
    phone = extract_phone(customer_texts)
    if phone:
        req.customer = req.customer | {"phone": phone}
    req.missing = missing_fields(ctx.required, req.items, req.customer)


def _option(variant: dict) -> str:
    return " ".join(str(v) for v in (variant.get("size"), variant.get("color")) if v)


def _match_variant(wanted: str, options: list[str]) -> str | None:
    """Chỉ nhận biến thể có thật trong catalog (so không dấu, không phân biệt thứ tự từ)."""
    w = set(fold(wanted).replace("/", " ").split())
    for o in options:
        if w and set(fold(o).split()) == w:
            return o
    return None


def apply_draft(ctx: OrderContext, order: OrderFields, products: dict[str, dict]) -> OrderRequest:
    """Phiếu sau lượt này. `products`: sku ⇒ bản ghi tool đã trả trong run."""
    req = replace(ctx.request, items=[dict(i) for i in ctx.request.items])
    product = products.get(order.sku.strip())
    # Chỉ nhận biến thể còn hàng; món hết sạch không vào phiếu (bot gợi ý mẫu khác).
    in_stock = [v for v in (product or {}).get("variants", []) if (v.get("stock") or 0) > 0]
    if product is not None and in_stock:
        options = [o for o in (_option(v) for v in in_stock) if o]
        same = req.items and req.items[0]["sku"] == product["sku"]
        item = req.items[0] if same else {}
        req.items = [
            {
                "catalog_item_id": product["id"],
                "sku": product["sku"],
                "name": product["name"],
                "unit_price_vnd": int(product["price_vnd"]),
                "options": options,
                "needs_variant": len(options) > 1,
                "variant": item.get("variant") or (options[0] if len(options) == 1 else None),
                "quantity": item.get("quantity") or 0,
                "url": product.get("url"),
            }
        ]
    if req.items:
        item = req.items[0]
        variant = _match_variant(order.variant, item.get("options", []))
        if variant:
            item["variant"] = variant
        if order.quantity:
            item["quantity"] = order.quantity
    for f in TEXT_FIELDS:
        value = getattr(order, f).strip()
        if value:
            req.customer = req.customer | {f: value}
            req.unverified = sorted(set(req.unverified) | {f})
    required, req.destination, _ = plan(ctx.config, ctx.provider, req.items)
    req.missing = missing_fields(required, req.items, req.customer)
    return req
