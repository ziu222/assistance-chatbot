"""Việc định kỳ ghép nháp với câu nhân viên và chấm bằng code (thiết kế 04 S1).

Chỉ đọc đường trả lời: không đổi nháp, hội thoại hay tin nhắn. Nháp chưa đủ để kết luận (`pending`)
không tạo dòng nào và được xem lại ở lượt sau; quá 24 giờ thì luôn kết luận được. Chạy lần đầu
tự xử lý toàn bộ nháp cũ (backfill). Khóa nháp bằng SKIP LOCKED nên nhiều worker không chấm trùng.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.validation import extract_amounts
from app.modules.shadow.domain.checks import Product, check
from app.modules.shadow.domain.judge import finalize
from app.modules.shadow.domain.pairing import Msg, pair
from app.shared.infrastructure.metrics import SHADOW_COMPARISONS, SHADOW_PAIRING_LAG

log = logging.getLogger(__name__)
PAGE = 200
MAX_MESSAGES_AFTER = 50  # câu tham chiếu nằm ngay sau nháp; không cần đọc cả hội thoại

_DRAFTS = text(
    """
    SELECT d.id, d.tenant_id, d.conversation_id, d.run_id, d.based_on_sequence, d.text,
           d.sources, d.grounded, d.needs_human, d.created_at, d.shown_at,
           r.intents, r.knowledge_hash, r.prompt_version,
           v.config->>'key' AS template_key, v.config->>'version' AS template_version,
           (SELECT st.detail->>'model' FROM agent_run_steps st
             WHERE st.tenant_id = r.tenant_id AND st.run_id = r.id AND st.kind = 'llm'
             ORDER BY st.step_index DESC LIMIT 1) AS model
    FROM agent_drafts d
    JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id
    JOIN agent_versions v ON v.tenant_id = r.tenant_id AND v.id = r.agent_version_id
    WHERE NOT EXISTS (SELECT 1 FROM shadow_comparisons c WHERE c.draft_id = d.id)
      AND (d.created_at, d.id) > (:after_at, :after_id)
    ORDER BY d.created_at, d.id
    LIMIT :n
    FOR UPDATE OF d SKIP LOCKED
    """
)


@dataclass
class _Shop:
    """Catalog và số tiền hợp lệ của một tenant, đọc một lần mỗi lượt."""

    by_id: dict[str, Product] = field(default_factory=dict)
    known: set[int] = field(default_factory=set)


async def _shop(s: AsyncSession, tenant_id: UUID) -> _Shop:
    shop = _Shop()
    rows = await s.execute(
        text("SELECT id, sku, name, price_vnd FROM catalog_items WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    for r in rows:
        shop.by_id[str(r.id)] = Product(r.sku, r.name)
        shop.known.add(int(r.price_vnd))
    knowledge = await s.execute(
        text(
            "SELECT question || ' ' || answer FROM faq_entries WHERE tenant_id = :t "
            "UNION ALL SELECT title || ' ' || body FROM knowledge_docs WHERE tenant_id = :t"
        ),
        {"t": tenant_id},
    )
    for (body,) in knowledge:
        shop.known |= extract_amounts(body)
    return shop


async def _messages_after(s: AsyncSession, d: Any) -> list[Msg]:
    rows = await s.execute(
        text(
            "SELECT sequence, sender_type, content, occurred_at FROM messages "
            "WHERE tenant_id = :t AND conversation_id = :c AND sequence > :seq "
            "ORDER BY sequence LIMIT :n"
        ),
        {
            "t": d.tenant_id,
            "c": d.conversation_id,
            "seq": d.based_on_sequence,
            "n": MAX_MESSAGES_AFTER,
        },
    )
    return [Msg(r.sequence, r.sender_type, r.content, r.occurred_at) for r in rows]


async def _compare(s: AsyncSession, d: Any, shop: _Shop, now: datetime) -> str | None:
    """Ghi một dòng so sánh; trả status, hoặc None nếu chưa kết luận được."""
    p = pair(d.based_on_sequence, d.created_at, await _messages_after(s, d), now)
    if p.status == "pending":
        return None
    checks = None
    if p.status == "paired":
        skus = {
            shop.by_id[src["id"]].sku
            for src in d.sources
            if src.get("type") == "product" and src.get("id") in shop.by_id
        }
        checks = check(
            draft_text=d.text,
            draft_skus=skus,
            grounded=d.grounded,
            needs_human=d.needs_human,
            reference=p.text,
            known_amounts=shop.known,
            catalog=list(shop.by_id.values()),
        )
    final = finalize(checks.verdict if checks else None, None)
    await s.execute(
        text(
            "INSERT INTO shadow_comparisons (id, tenant_id, conversation_id, draft_id, run_id, "
            "status, reference_from_seq, reference_to_seq, intent, config_version, code_checks, "
            "code_verdict, bot_lead_seconds, final_verdict, sendable, draft_exposure) "
            "VALUES (:id, :t, :c, :d, :r, :st, :rf, :rt, :i, CAST(:cv AS jsonb), "
            "CAST(:cc AS jsonb), :v, :lead, :fv, :sd, :exp)"
        ),
        {
            "id": uuid4(),
            "t": d.tenant_id,
            "c": d.conversation_id,
            "d": d.id,
            "r": d.run_id,
            "st": p.status,
            "rf": p.reference[0].sequence if p.reference else None,
            "rt": p.reference[-1].sequence if p.reference else None,
            "i": (d.intents or [None])[0],
            "cv": json.dumps(
                {
                    "template": d.template_key,
                    "template_version": d.template_version,
                    "prompt_version": d.prompt_version,
                    "model": d.model,
                    "knowledge_hash": d.knowledge_hash,
                }
            ),
            "cc": json.dumps(checks.as_dict()) if checks else None,
            "v": checks.verdict if checks else None,
            "fv": final[0],
            # Nhân viên đã thấy nháp (Learning) ⇒ so sánh không còn "mù" (thiết kế 05 §7).
            "exp": "shown" if d.shown_at else "hidden",
            "sd": final[1],
            "lead": int((p.first_reply_at - d.created_at).total_seconds())
            if p.first_reply_at
            else None,
        },
    )
    SHADOW_COMPARISONS.labels(
        status=p.status, verdict=(checks.verdict if checks else None) or ""
    ).inc()
    if p.first_reply_at:
        SHADOW_PAIRING_LAG.observe((now - p.first_reply_at).total_seconds())
    return p.status


async def compare_due(
    factory: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> dict[str, int]:
    """Một lượt qua mọi nháp chưa so sánh (theo trang). Trả số dòng ghi theo status."""
    now = now or datetime.now(UTC)
    done: dict[str, int] = {}
    after = (datetime(1970, 1, 1, tzinfo=UTC), UUID(int=0))
    shops: dict[UUID, _Shop] = {}
    while True:
        async with factory() as s, s.begin():
            drafts = (
                await s.execute(_DRAFTS, {"after_at": after[0], "after_id": after[1], "n": PAGE})
            ).all()
            if not drafts:
                return done
            for d in drafts:
                if d.tenant_id not in shops:
                    shops[d.tenant_id] = await _shop(s, d.tenant_id)
                status = await _compare(s, d, shops[d.tenant_id], now)
                if status:
                    done[status] = done.get(status, 0) + 1
            after = (drafts[-1].created_at, drafts[-1].id)


async def run_shadow_comparisons(
    factory: async_sessionmaker[AsyncSession], stop: asyncio.Event, tick_s: float = 60.0
) -> None:
    while not stop.is_set():
        try:
            done = await compare_due(factory)
            if done:
                log.info("shadow comparisons", extra=done)
        except Exception:
            log.exception("shadow comparison tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=tick_s)
