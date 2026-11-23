"""Kiểu dữ liệu model trả về (structured output). Thiết kế bước 2 §6.5."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassifyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intents: list[str] = Field(max_length=5)
    confidence_note: str = Field(max_length=300)
    # Tín hiệu leo thang (2.10): chỉ GHI vào trace để đo, không điều khiển gì (§12.7).
    frustration: Literal["none", "mild", "high"]
    human_decision: bool  # cần người quyết: ngoại lệ chính sách, giảm giá riêng, khiếu nại…


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["faq", "doc", "product"]
    id: str = Field(max_length=64)


class DraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(max_length=1200)  # tiếng Việt, ngắn
    sources: list[Source] = Field(max_length=10)
    needs_human: bool
    reason: Literal["", "no_source", "policy", "discount", "order", "complaint", "unclear"]


class OrderFields(BaseModel):
    """Phần đơn hàng model điền khi có khối <order_flow> (thiết kế 03 §5.2). Rỗng/0 = chưa biết.

    Không có số điện thoại: code tự tách từ tin của khách. `sku` chỉ được nhận nếu nằm trong kết
    quả tool của chính run đó; giá luôn lấy từ catalog.
    """

    model_config = ConfigDict(extra="forbid")
    sku: str = Field(max_length=64)
    variant: str = Field(max_length=100)
    quantity: int = Field(ge=0, le=999)
    name: str = Field(max_length=100)
    address: str = Field(max_length=300)
    delivery_time: str = Field(max_length=100)
    note: str = Field(max_length=300)


class OrderDraftOut(DraftOut):
    order: OrderFields
