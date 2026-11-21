"""Cổng rule xác định (bước 2 của một run): không gọi LLM, không tốn tiền (§7.3)."""

from dataclasses import dataclass
from typing import Literal

from app.modules.knowledge.domain.text import fold

# Đã fold (không dấu, chữ thường). ponytail: danh sách cố định; mở rộng khi eval cho thấy sót.
HANDOFF_PHRASES = (
    "gap nguoi",
    "gap nhan vien",
    "nhan vien",
    "nguoi that",
    "tu van vien",
    "chu shop",
    "quan ly",
    "khieu nai",
    "lua dao",
    "hoan tien",
)
GREETING_WORDS = frozenset("xin chao hello hi hey alo shop oi ban a da vang ok nhe nha".split())
THANKS_WORDS = frozenset("cam on thanks thank you".split())

# ponytail: câu mẫu cố định; chuyển vào template khi cần giọng riêng từng shop.
GREETING_REPLY = "Dạ em chào anh/chị ạ! Em có thể hỗ trợ gì cho anh/chị ạ?"
THANKS_REPLY = "Dạ không có gì ạ! Anh/chị cần em hỗ trợ thêm gì cứ nhắn em nhé."


@dataclass(frozen=True)
class RuleOutcome:
    kind: Literal["handoff", "canned"]
    reply: str = ""
    matched: str = ""


def apply_rules(customer_texts: list[str]) -> RuleOutcome | None:
    """`customer_texts`: các tin khách trong cụm `from..to`. None ⇒ đi tiếp xuống phân loại."""
    folded = [fold(t) for t in customer_texts if t.strip()]
    joined = " ".join(folded)
    for phrase in HANDOFF_PHRASES:
        if phrase in joined:
            return RuleOutcome("handoff", matched=phrase)
    words = {w.strip(".,!?;:") for w in joined.split()}
    words.discard("")
    if words and words <= GREETING_WORDS | THANKS_WORDS:
        return RuleOutcome("canned", reply=THANKS_REPLY if words & THANKS_WORDS else GREETING_REPLY)
    return None
