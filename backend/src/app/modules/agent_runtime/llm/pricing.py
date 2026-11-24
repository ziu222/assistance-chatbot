"""Giá theo (provider, model), đơn vị USD / 1 triệu token (PROJECT_CONTEXT §19.6).

QUAN TRỌNG: đây là giá niêm yết tại thời điểm `PRICE_VERSION`, phải đối chiếu trang giá chính
thức của từng nhà cung cấp trước khi deploy và mỗi khi đổi model. Qua gateway trung gian, hóa
đơn thực tế có thể khác (gateway chèn thêm token). Đổi giá = đổi PRICE_VERSION; `usage_events`
lưu phiên bản đã dùng để tính lại được.

1 USD / 1 triệu token = 1 micro-USD / token, nên chi phí micro-USD = Σ token × giá.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from app.modules.agent_runtime.llm.port import Usage

PRICE_VERSION = "2026-11-24"


@dataclass(frozen=True)
class Price:
    input: Decimal  # token vào không nằm trong cache
    cache_read: Decimal
    cache_write: Decimal
    output: Decimal


def _p(inp: str, cache_read: str, cache_write: str, out: str) -> Price:
    return Price(Decimal(inp), Decimal(cache_read), Decimal(cache_write), Decimal(out))


PRICES: dict[tuple[str, str], Price] = {
    # Anthropic (giá niêm yết đã ghi nhận 2026-11-03; cache ghi = 1,25x cho TTL 5 phút)
    ("anthropic", "claude-sonnet-5"): _p("2", "0.20", "2.50", "10"),
    ("anthropic", "claude-opus-5-5"): _p("4", "0.20", "5", "20"),
    ("anthropic", "claude-sonnet-5-5"): _p("2", "0.10", "2.50", "10"),  # giám khảo (bước 3)
    ("anthropic", "claude-haiku-5-5"): _p("0.10", "0.01", "0.125", "0.50"),
    # OpenAI (giá niêm yết GPT-4.1, nhiều nguồn khớp nhau; CHƯA đối chiếu trang chính thức)
    ("openai", "gpt-4.1"): _p("2", "0.50", "0", "8"),
    ("openai", "gpt-4.1-mini"): _p("0.40", "0.10", "0", "1.60"),
    ("openai", "gpt-4o-mini"): _p("0.15", "0.075", "0", "0.60"),
}


class UnknownPrice(KeyError):
    pass


def cost_micro(provider: str, model: str, usage: Usage) -> int:
    """Chi phí micro-USD, làm tròn LÊN. Model chưa có giá ⇒ lỗi, không bao giờ ngầm tính 0
    (nếu không, trần chi phí ngày bị lách)."""
    try:
        price = PRICES[(provider, model)]
    except KeyError:
        raise UnknownPrice(f"no price for {provider}:{model}") from None
    total = (
        usage.input * price.input
        + usage.cache_read * price.cache_read
        + usage.cache_write * price.cache_write
        + usage.output * price.output
    )
    return math.ceil(total)
