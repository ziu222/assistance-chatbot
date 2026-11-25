"""Bộ dựng ứng viên tri thức định kỳ từ bản ghi sửa của nhân viên (thiết kế 05 L3).

Bất biến E17: file này CHỈ đọc tin đã gửi của nhân viên (`sender_type = 'human'`) và tri thức hiện
có của shop — không bao giờ truy vấn tin của khách hàng (được khóa bởi `test_architecture.py`).
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.usage import (
    LEARNING,
    record_usage,
    spent_today_micro,
)
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    BadRequest,
    DeadlineExceeded,
    InvalidOutput,
    LLMPort,
    LLMRequest,
    ProviderUnavailable,
    Turn,
)
from app.modules.agent_runtime.llm.pricing import UnknownPrice
from app.modules.authoring.infrastructure.repository import current_profile
from app.modules.knowledge.domain.topics import suggest_topics
from app.modules.knowledge.infrastructure.releases import active_snapshot
from app.modules.learning.domain.candidates import (
    SYSTEM,
    CandidateOut,
    EvidenceItem,
    fingerprint,
    has_enough_evidence,
    is_significant,
    strip_pii,
    user_turn,
)
from app.modules.learning.domain.suggestions import (
    BrandUseItem,
    OrderSignalItem,
    pick_brand_examples,
    suggest_order_flow_fields,
)
from app.modules.learning.infrastructure.checks import verify_candidate
from app.modules.learning.infrastructure.review import (
    evaluate_manual_releases,
    process_approved_candidates,
)
from app.modules.orders.domain.order_flow import OrderFlowConfig
from app.modules.orders.infrastructure.repository import current_flow

log = logging.getLogger(__name__)
DEADLINE_S = 60
LOOKBACK_DAYS = 14
REJECT_COOLDOWN_DAYS = 30
# Khóa advisory cố định để nhiều replica `agent_worker` không chạy gom ứng viên trùng nhau.
_ADVISORY_LOCK_KEY = 4_050_301

_RECENT_USES = text(
    """
    SELECT u.draft_id, u.tenant_id, u.conversation_id, u.message_id, u.action, u.diff_features,
           r.intents, m.content AS staff_text
    FROM draft_uses u
    JOIN agent_drafts d ON d.tenant_id = u.tenant_id AND d.id = u.draft_id
    JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id
    JOIN messages m
      ON m.tenant_id = u.tenant_id AND m.id = u.message_id AND m.sender_type = 'human'
    WHERE u.action IN ('edited', 'discarded')
      AND u.created_at > now() - make_interval(days => :days)
      AND m.content IS NOT NULL AND m.content <> ''
    ORDER BY u.created_at ASC
    """
)


async def _used_draft_ids(s: AsyncSession, tenant_id: UUID) -> set[str]:
    rows = await s.execute(
        text(
            "SELECT e->>'draft_id' FROM knowledge_candidates c, "
            "LATERAL jsonb_array_elements(c.evidence) AS e "
            "WHERE c.tenant_id = :t AND c.status IN ('pending','approved','published')"
        ),
        {"t": tenant_id},
    )
    return {r[0] for r in rows if r[0]}


async def _fingerprint_blocked(s: AsyncSession, tenant_id: UUID, fp: str) -> bool:
    row = (
        await s.execute(
            text(
                "SELECT 1 FROM knowledge_candidates "
                "WHERE tenant_id = :t AND fingerprint = :fp "
                "  AND (status IN ('pending','approved','published') "
                "       OR (status = 'rejected' "
                "           AND created_at > now() - make_interval(days => :days))) "
                "LIMIT 1"
            ),
            {"t": tenant_id, "fp": fp, "days": REJECT_COOLDOWN_DAYS},
        )
    ).first()
    return row is not None


async def build_candidates_once(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMPort,
    *,
    daily_cap_micro: int,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Gom bản ghi sửa 14 ngày qua và sinh ứng viên tri thức mới. Trả số ứng viên đã tạo."""
    async with session_factory() as s:
        rows = (await s.execute(_RECENT_USES, {"days": LOOKBACK_DAYS})).all()

    grouped: dict[tuple[UUID, str, str], list[EvidenceItem]] = defaultdict(list)
    for r in rows:
        intent = r.intents[0] if r.intents else "other"
        topics = suggest_topics(r.staff_text)
        topic = topics[0] if topics else "other"
        grouped[(r.tenant_id, intent, topic)].append(
            EvidenceItem(
                draft_id=str(r.draft_id),
                conversation_id=str(r.conversation_id),
                message_id=str(r.message_id) if r.message_id else None,
                intent=intent,
                topic=topic,
                action=r.action,
                diff_features=r.diff_features or {},
                staff_text=r.staff_text,
            )
        )

    created = 0
    used_cache: dict[UUID, set[str]] = {}
    for (tenant_id, intent, topic), items in grouped.items():
        async with session_factory() as s:
            if tenant_id not in used_cache:
                used_cache[tenant_id] = await _used_draft_ids(s, tenant_id)
            fresh = [i for i in items if i.draft_id not in used_cache[tenant_id]]
            if not has_enough_evidence(fresh):
                continue
            if await spent_today_micro(s, tenant_id, purpose=LEARNING) >= daily_cap_micro:
                continue
            snapshot = await active_snapshot(s, tenant_id)

        sig = [i for i in fresh if is_significant(i)]
        existing_faqs = [(f.id, f.first, f.second) for f in snapshot.faqs]
        staff_texts = [i.staff_text for i in sig]
        try:
            res = await llm.complete(
                LLMRequest(
                    route=LEARNING,
                    system_blocks=(SYSTEM,),
                    turns=(
                        Turn(
                            role="user", text=user_turn(intent, topic, staff_texts, existing_faqs)
                        ),
                    ),
                    output_schema=CandidateOut,
                    deadline=clock() + DEADLINE_S,
                )
            )
        except (
            AllTargetsFailed,
            ProviderUnavailable,
            DeadlineExceeded,
            BadRequest,
            InvalidOutput,
            UnknownPrice,
        ) as exc:
            log.warning(
                "learning candidate generation failed",
                extra={"tenant_id": str(tenant_id), "intent": intent, "error": type(exc).__name__},
            )
            continue

        parsed = res.parsed
        if not isinstance(parsed, CandidateOut):
            continue

        title, _ = strip_pii(parsed.title)
        body, _ = strip_pii(parsed.body)
        if not title or not body:
            continue

        fp = fingerprint(parsed.kind, intent, topic, f"{title} {body}")
        replaces: UUID | None = None
        if parsed.kind == "faq_update" and parsed.replaces_id:
            valid_ids = {f.id for f in snapshot.faqs}
            if parsed.replaces_id in valid_ids:
                replaces = UUID(parsed.replaces_id)

        proposal = (
            {"title": title, "body": body}
            if parsed.kind == "doc_section"
            else {"question": title, "answer": body}
        )
        evidence = [
            {
                "draft_id": i.draft_id,
                "conversation_id": i.conversation_id,
                "message_id": i.message_id,
            }
            for i in sig
        ]
        conv_ids = [UUID(i.conversation_id) for i in sig]

        async with session_factory() as s, s.begin():
            await record_usage(s, tenant_id=tenant_id, run_id=None, route=LEARNING, result=res)
            if await _fingerprint_blocked(s, tenant_id, fp):
                continue
            checks = await verify_candidate(
                s,
                tenant_id,
                conv_ids,
                proposal_text=f"{title}\n{body}",
                staff_texts=staff_texts,
            )
            await s.execute(
                text(
                    "INSERT INTO knowledge_candidates "
                    "(id, tenant_id, kind, intent, topic, fingerprint, proposal, replaces, "
                    " evidence, checks, status) "
                    "VALUES (:id, :t, :k, :intent, :topic, :fp, CAST(:prop AS jsonb), :rep, "
                    " CAST(:ev AS jsonb), CAST(:chk AS jsonb), 'pending')"
                ),
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "k": parsed.kind,
                    "intent": intent,
                    "topic": topic,
                    "fp": fp,
                    "prop": json.dumps(proposal, ensure_ascii=False),
                    "rep": replaces,
                    "ev": json.dumps(evidence),
                    "chk": json.dumps(checks.as_dict(), ensure_ascii=False),
                },
            )
        used_cache[tenant_id].update(i.draft_id for i in sig)
        created += 1

    return created


_AS_IS_USES = text(
    """
    SELECT u.draft_id, u.tenant_id, u.conversation_id, u.message_id, r.intents,
           m.content AS staff_text
    FROM draft_uses u
    JOIN agent_drafts d ON d.tenant_id = u.tenant_id AND d.id = u.draft_id
    JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id
    JOIN messages m
      ON m.tenant_id = u.tenant_id AND m.id = u.message_id AND m.sender_type = 'human'
    WHERE u.action = 'as_is'
      AND u.created_at > now() - make_interval(days => :days)
      AND m.content IS NOT NULL AND m.content <> ''
    ORDER BY u.created_at ASC
    """
)

_ORDER_CONFIRMED_SIGNALS = text(
    """
    SELECT tenant_id, conversation_id, payload
    FROM learning_signals
    WHERE kind = 'order_confirmed'
      AND created_at > now() - make_interval(days => :days)
    ORDER BY created_at ASC
    """
)


async def build_brand_examples_once(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Đề xuất câu mẫu giọng văn (`brand_example`) từ các nháp được dùng nguyên văn nhiều lần."""
    async with session_factory() as s:
        rows = (await s.execute(_AS_IS_USES, {"days": LOOKBACK_DAYS})).all()

    by_tenant: dict[UUID, list[BrandUseItem]] = defaultdict(list)
    for r in rows:
        intent = r.intents[0] if r.intents else "other"
        by_tenant[r.tenant_id].append(
            BrandUseItem(
                draft_id=str(r.draft_id),
                conversation_id=str(r.conversation_id),
                message_id=str(r.message_id) if r.message_id else None,
                intent=intent,
                staff_text=r.staff_text,
            )
        )

    created = 0
    for tenant_id, items in by_tenant.items():
        async with session_factory() as s, s.begin():
            prof = await current_profile(s, tenant_id)
            existing_samples = prof[1].samples if prof else []
            specs = pick_brand_examples(items, existing_samples=existing_samples)
            for spec in specs:
                fp = fingerprint("brand_example", spec.intent, "brand", spec.sample)
                if await _fingerprint_blocked(s, tenant_id, fp):
                    continue
                conv_ids = [UUID(e["conversation_id"]) for e in spec.evidence]
                checks = await verify_candidate(
                    s,
                    tenant_id,
                    conv_ids,
                    proposal_text=spec.sample,
                    staff_texts=[spec.sample],
                )
                proposal = {
                    "title": f"Câu mẫu giọng văn ({spec.intent})",
                    "body": spec.sample,
                }
                await s.execute(
                    text(
                        "INSERT INTO knowledge_candidates "
                        "(id, tenant_id, kind, intent, topic, fingerprint, proposal, replaces, "
                        " evidence, checks, status) "
                        "VALUES (:id, :t, 'brand_example', :intent, 'brand', :fp, "
                        " CAST(:prop AS jsonb), NULL, CAST(:ev AS jsonb), "
                        " CAST(:chk AS jsonb), 'pending')"
                    ),
                    {
                        "id": uuid4(),
                        "t": tenant_id,
                        "intent": spec.intent,
                        "fp": fp,
                        "prop": json.dumps(proposal, ensure_ascii=False),
                        "ev": json.dumps(list(spec.evidence)),
                        "chk": json.dumps(checks.as_dict(), ensure_ascii=False),
                    },
                )
                created += 1
    return created


async def build_order_flow_candidates_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Đề xuất thêm trường vào quy trình đặt hàng từ tín hiệu `order_confirmed` (03 O6, 05 L6)."""
    async with session_factory() as s:
        rows = (await s.execute(_ORDER_CONFIRMED_SIGNALS, {"days": LOOKBACK_DAYS})).all()

    by_tenant: dict[UUID, list[OrderSignalItem]] = defaultdict(list)
    for r in rows:
        payload = r.payload or {}
        edited = tuple(str(x) for x in (payload.get("staff_edited") or ()))
        if not edited:
            continue
        by_tenant[r.tenant_id].append(
            OrderSignalItem(
                conversation_id=str(r.conversation_id) if r.conversation_id else "",
                order_request_id=str(payload.get("order_request_id") or ""),
                staff_edited=edited,
            )
        )

    created = 0
    for tenant_id, signals in by_tenant.items():
        async with session_factory() as s, s.begin():
            flow = await current_flow(s, tenant_id)
            current_required = (
                list(flow.config.required_fields)
                if flow
                else list(OrderFlowConfig().required_fields)
            )
            specs = suggest_order_flow_fields(signals, current_required=current_required)
            for spec in specs:
                fp = fingerprint("order_flow", "order_intent", spec.field, spec.field)
                if await _fingerprint_blocked(s, tenant_id, fp):
                    continue
                proposal = {
                    "field": spec.field,
                    "label": spec.label,
                    "title": f"Thêm trường '{spec.label}' vào quy trình đặt hàng",
                    "body": (
                        f"Nhân viên đã điền/sửa thêm trường '{spec.label}' {spec.count} lần "
                        "khi xác nhận đơn trong 14 ngày qua."
                    ),
                }
                checks = {
                    "passed": True,
                    "ungrounded_facts": {},
                    "pii": [],
                    "customer_overlap": False,
                }
                await s.execute(
                    text(
                        "INSERT INTO knowledge_candidates "
                        "(id, tenant_id, kind, intent, topic, fingerprint, proposal, replaces, "
                        " evidence, checks, status) "
                        "VALUES (:id, :t, 'order_flow', 'order_intent', :topic, :fp, "
                        " CAST(:prop AS jsonb), NULL, CAST(:ev AS jsonb), "
                        " CAST(:chk AS jsonb), 'pending')"
                    ),
                    {
                        "id": uuid4(),
                        "t": tenant_id,
                        "topic": spec.field,
                        "fp": fp,
                        "prop": json.dumps(proposal, ensure_ascii=False),
                        "ev": json.dumps(list(spec.evidence)),
                        "chk": json.dumps(checks),
                    },
                )
                created += 1
    return created


class LearningWorker:
    """Việc định kỳ (mỗi giờ) gom bản ghi sửa thành ứng viên tri thức (thiết kế 05 §6.2)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMPort,
        *,
        daily_cap_micro: int,
        interval_s: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory, self._llm = session_factory, llm
        self._cap, self._interval, self._clock = daily_cap_micro, interval_s, clock

    async def process_once(self) -> int:
        async with self._factory() as lock_s:
            locked = (
                await lock_s.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
                )
            ).scalar_one()
            if not locked:
                return 0
            try:
                created = await build_candidates_once(
                    self._factory, self._llm, daily_cap_micro=self._cap, clock=self._clock
                )
                created += await build_brand_examples_once(self._factory)
                created += await build_order_flow_candidates_once(self._factory)
                await process_approved_candidates(
                    self._factory, self._llm, daily_cap_micro=self._cap, clock=self._clock
                )
                await evaluate_manual_releases(
                    self._factory, self._llm, daily_cap_micro=self._cap, clock=self._clock
                )
                return created
            finally:
                await lock_s.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY}
                )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.process_once()
            except Exception:
                log.exception("learning worker iteration crashed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
