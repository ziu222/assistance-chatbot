"""Ảnh chụp bản phát hành tri thức và dựng khối `<knowledge>` xác định (thiết kế 05 L2): hàm thuần.

Cùng ảnh chụp ⇒ cùng khối `<knowledge>` và cùng `knowledge_hash` (ADR-0038).
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from app.modules.knowledge.domain.topics import covered, effective_topics, missing_line

ReleaseReason = Literal["manual_edit", "lesson"]


@dataclass(frozen=True)
class SnapshotItem:
    id: str
    first: str  # question | title
    second: str  # answer | body
    topics: tuple[str, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "first": self.first,
            "second": self.second,
            "topics": list(self.topics) if self.topics is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotItem":
        raw_topics = data.get("topics")
        return cls(
            id=str(data["id"]),
            first=str(data["first"]),
            second=str(data["second"]),
            topics=tuple(raw_topics) if raw_topics is not None else None,
        )


@dataclass(frozen=True)
class KnowledgeSnapshot:
    faqs: tuple[SnapshotItem, ...]
    docs: tuple[SnapshotItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "faqs": [e.as_dict() for e in self.faqs],
            "docs": [e.as_dict() for e in self.docs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeSnapshot":
        faqs = sorted((SnapshotItem.from_dict(x) for x in data.get("faqs", ())), key=lambda e: e.id)
        docs = sorted((SnapshotItem.from_dict(x) for x in data.get("docs", ())), key=lambda e: e.id)
        return cls(faqs=tuple(faqs), docs=tuple(docs))


@dataclass(frozen=True)
class RenderedKnowledge:
    block: str
    hash: str
    sources: frozenset[tuple[str, str]]
    text: str


def build_snapshot(faqs: Iterable[SnapshotItem], docs: Iterable[SnapshotItem]) -> KnowledgeSnapshot:
    """Sắp xếp cố định theo ID để ảnh chụp và `knowledge_hash` hoàn toàn xác định."""
    return KnowledgeSnapshot(
        faqs=tuple(sorted(faqs, key=lambda e: e.id)),
        docs=tuple(sorted(docs, key=lambda e: e.id)),
    )


def render_knowledge(snapshot: KnowledgeSnapshot) -> RenderedKnowledge:
    """Dựng khối `<knowledge>` và `knowledge_hash` từ ảnh chụp bản phát hành."""
    parts = [f'<faq id="{e.id}">\nQ: {e.first}\nA: {e.second}\n</faq>' for e in snapshot.faqs]
    parts += [
        f'<doc id="{e.id}" title="{escape(e.first)}">\n{e.second}\n</doc>' for e in snapshot.docs
    ]
    entries = [*snapshot.faqs, *snapshot.docs]
    gap = missing_line(
        covered(
            effective_topics(
                list(e.topics) if e.topics is not None else None, f"{e.first} {e.second}"
            )
            for e in entries
        )
    )
    if gap:
        parts.append(f"<missing>{gap}</missing>")
    block = "<knowledge>\n" + "\n".join(parts) + "\n</knowledge>"
    return RenderedKnowledge(
        block=block,
        hash=hashlib.sha256(block.encode()).hexdigest(),
        sources=frozenset(
            [("faq", e.id) for e in snapshot.faqs] + [("doc", e.id) for e in snapshot.docs]
        ),
        text="\n".join(f"{e.first} {e.second}" for e in entries),
    )
