"""Tool read-only của agent (thiết kế bước 2 §6.3, PROJECT_CONTEXT §9.8).

Model chỉ ĐỀ XUẤT tên tool + tham số. Backend validate schema, ép tenant từ run (không bao giờ từ
model), giới hạn số bản ghi và chiếu kết quả còn vài trường cần cho câu trả lời.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_runtime.llm.port import ToolSpec
from app.modules.knowledge.infrastructure.catalog import (
    CatalogItem,
    get_catalog_item,
    search_catalog,
)

DESCRIPTION_LIMIT = 500
STALE_AFTER = timedelta(hours=24)  # nguồn ngoài chưa sync quá lâu ⇒ trace gắn cờ (§13.2)


class _SearchProducts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=3, ge=1, le=5)


class _GetProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID


# Thứ tự cố định: đổi thứ tự là mất prompt cache (thiết kế §6.4).
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": (
            "Tìm sản phẩm trong catalog của shop theo tên hoặc mô tả (gõ có dấu hay không dấu "
            "đều được). Trả về giá (VND) và các biến thể size/màu kèm số lượng tồn. Dùng trước "
            "khi nói bất kỳ điều gì về giá, size hoặc còn hàng."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tên/mô tả sản phẩm khách hỏi"},
                "limit": {"type": "integer", "description": "Số kết quả tối đa (1-5)"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product",
        "description": (
            "Lấy chi tiết một sản phẩm (kèm mô tả) theo id đã nhận từ search_products."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "string", "description": "id sản phẩm"}},
            "required": ["product_id"],
            "additionalProperties": False,
        },
    },
]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None  # invalid_arguments | not_found | unknown_tool
    stale: bool = False  # có sản phẩm từ nguồn ngoài chưa sync trong STALE_AFTER


def _stale(items: list[CatalogItem]) -> bool:
    # CSV do chủ shop tự tải lên: không có "nguồn" để mà cũ.
    cutoff = datetime.now(UTC) - STALE_AFTER
    return any(i.source != "csv" and i.synced_at and i.synced_at < cutoff for i in items)


def _project(item: CatalogItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sku": item.sku,
        "name": item.name,
        "price_vnd": item.price_vnd,
        "variants": item.variants,
        "synced_at": item.synced_at.isoformat(timespec="minutes") if item.synced_at else None,
    } | ({"url": item.url} if item.url else {})  # link trang sản phẩm, nếu nguồn có


async def run_tool(s: AsyncSession, tenant_id: UUID, name: str, args: dict[str, Any]) -> ToolResult:
    """Chạy tool với tenant của run. Lỗi trả về như dữ liệu (để model đọc), không ném."""
    try:
        if name == "search_products":
            p = _SearchProducts.model_validate(args)
            items = await search_catalog(s, tenant_id, p.query, p.limit)
            return ToolResult(ok=True, data=[_project(i) for i in items], stale=_stale(items))
        if name == "get_product":
            g = _GetProduct.model_validate(args)
            item = await get_catalog_item(s, tenant_id, g.product_id)
            if item is None:  # không có, hoặc của tenant khác: không phân biệt
                return ToolResult(ok=False, error="not_found")
            description = (item.description or "")[:DESCRIPTION_LIMIT]
            return ToolResult(
                ok=True, data=_project(item) | {"description": description}, stale=_stale([item])
            )
    except ValidationError:
        return ToolResult(ok=False, error="invalid_arguments")
    return ToolResult(ok=False, error="unknown_tool")


# Dạng trung lập của TOOL_SPECS cho LLMPort (strict luôn bật; adapter tự dịch).
TOOLS: list[ToolSpec] = [
    ToolSpec(s["name"], s["description"], s["input_schema"]) for s in TOOL_SPECS
]
