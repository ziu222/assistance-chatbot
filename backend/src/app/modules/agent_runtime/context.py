"""ContextBuilder xác định: dựng toàn bộ ngữ cảnh từ Postgres cho mỗi run (ADR-0030).

Mô hình không nhớ gì; hệ thống nhớ. Cùng DB + cùng yêu cầu ⇒ cùng prompt, nên đổi provider giữa
chừng không làm "bot quên". Khối ổn định (FAQ/tài liệu sắp theo ID) đặt trước phần thay đổi.
"""

from dataclasses import dataclass
from html import escape
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.authoring.domain.writing import BrandProfile, brand_voice_block
from app.modules.knowledge.domain.releases import render_knowledge
from app.modules.knowledge.infrastructure.releases import active_snapshot

HISTORY_TURNS = 10
MAX_NEW_MESSAGES = 50
MAX_MESSAGE_CHARS = 2000


@dataclass(frozen=True)
class Line:
    sequence: int
    who: str  # khach | nhan_vien
    content: str


@dataclass(frozen=True)
class RunContext:
    knowledge_block: str
    knowledge_hash: str
    knowledge_sources: frozenset[tuple[str, str]]  # (type, id) hợp lệ đã nằm trong prompt
    knowledge_text: str  # để rút số tiền hợp lệ ở bước kiểm tra
    history: tuple[Line, ...]
    new: tuple[Line, ...]
    last_sequence: int  # conversations.last_sequence lúc bắt đầu (bất biến 3)
    brand_voice_block: str = ""

    @property
    def customer_texts(self) -> list[str]:
        return [m.content for m in self.new if m.who == "khach"]

    def transcript(self) -> str:
        """Đưa vào MỘT khối dữ liệu có nhãn; ký tự < > bị escape nên khách không đóng thẻ được."""

        def fmt(lines: tuple[Line, ...]) -> str:
            return "\n".join(f"[{m.who}] {escape(m.content, quote=False)}" for m in lines)

        return (
            "<customer_messages>\n<history>\n"
            f"{fmt(self.history)}\n</history>\n<new>\n{fmt(self.new)}\n</new>\n"
            "</customer_messages>"
        )


_MESSAGES_SQL = """
    SELECT sequence, sender_type, content FROM messages
    WHERE tenant_id = :t AND conversation_id = :c AND content IS NOT NULL AND content <> ''
      AND {cond}
    ORDER BY sequence {order} LIMIT :n
"""


def _messages(cond: str, order: str):
    # cond/order là hằng của file này, không bao giờ lấy từ input.
    return text(_MESSAGES_SQL.format(cond=cond, order=order))


def _line(row) -> Line:
    who = "khach" if row.sender_type == "customer" else "nhan_vien"
    return Line(row.sequence, who, row.content[:MAX_MESSAGE_CHARS])


async def build_context(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, from_sequence: int, to_sequence: int
) -> RunContext:
    base = {"t": tenant_id, "c": conversation_id}
    last_sequence = (
        await s.execute(
            text("SELECT last_sequence FROM conversations WHERE tenant_id = :t AND id = :c"), base
        )
    ).scalar_one()
    history_rows = await s.execute(
        _messages("sequence < :frm", "DESC"),
        base | {"frm": from_sequence, "n": HISTORY_TURNS},
    )
    new_rows = await s.execute(
        _messages("sequence BETWEEN :frm AND :to", "ASC"),
        base | {"frm": from_sequence, "to": to_sequence, "n": MAX_NEW_MESSAGES},
    )
    history = tuple(reversed([_line(r) for r in history_rows]))

    rendered = render_knowledge(await active_snapshot(s, tenant_id))
    brand_row = (
        await s.execute(
            text(
                "SELECT config FROM brand_profiles WHERE tenant_id = :t "
                "ORDER BY number DESC LIMIT 1"
            ),
            {"t": tenant_id},
        )
    ).first()
    profile: BrandProfile | None = None
    if brand_row is not None:
        try:
            profile = BrandProfile.model_validate(brand_row[0])
        except ValidationError:
            profile = None
    return RunContext(
        knowledge_block=rendered.block,
        knowledge_hash=rendered.hash,
        knowledge_sources=rendered.sources,
        knowledge_text=rendered.text,
        history=history,
        new=tuple(_line(r) for r in new_rows),
        last_sequence=last_sequence,
        brand_voice_block=brand_voice_block(profile),
    )
