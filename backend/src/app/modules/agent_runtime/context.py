"""ContextBuilder xác định: dựng toàn bộ ngữ cảnh từ Postgres cho mỗi run (ADR-0030).

Mô hình không nhớ gì; hệ thống nhớ. Cùng DB + cùng yêu cầu ⇒ cùng prompt, nên đổi provider giữa
chừng không làm "bot quên". Khối ổn định (FAQ/tài liệu sắp theo ID) đặt trước phần thay đổi.
"""

import hashlib
from dataclasses import dataclass
from html import escape
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.infrastructure.content import list_entries

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

    faqs = sorted(
        (e for e in await list_entries(s, "faq", tenant_id) if e.status == "approved"),
        key=lambda e: str(e.id),
    )
    docs = sorted(
        (e for e in await list_entries(s, "doc", tenant_id) if e.status == "approved"),
        key=lambda e: str(e.id),
    )
    parts = [f'<faq id="{e.id}">\nQ: {e.first}\nA: {e.second}\n</faq>' for e in faqs]
    parts += [f'<doc id="{e.id}" title="{escape(e.first)}">\n{e.second}\n</doc>' for e in docs]
    block = "<knowledge>\n" + "\n".join(parts) + "\n</knowledge>"
    return RunContext(
        knowledge_block=block,
        knowledge_hash=hashlib.sha256(block.encode()).hexdigest(),
        knowledge_sources=frozenset(
            [("faq", str(e.id)) for e in faqs] + [("doc", str(e.id)) for e in docs]
        ),
        knowledge_text="\n".join(f"{e.first} {e.second}" for e in [*faqs, *docs]),
        history=history,
        new=tuple(_line(r) for r in new_rows),
        last_sequence=last_sequence,
    )
