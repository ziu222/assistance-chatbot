"""Duyệt/bỏ ứng viên tri thức và cổng eval hồi quy trước khi phát hành (thiết kế 05 L4).

Chủ shop duyệt (hoặc sửa rồi duyệt) ⇒ `approved` ⇒ cổng eval chạy ca hồi quy mới + các ca hồi quy
cũ trên bản nháp phát hành ⇒ đạt mới chuyển con trỏ `tenant_knowledge` (`published`), hỏng thì
`eval_failed` kèm ca lỗi để chủ shop sửa lại.
"""

import json
import logging
import time
from collections.abc import Callable
from html import escape
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
from app.modules.authoring.domain.writing import BrandProfile
from app.modules.authoring.infrastructure.repository import current_profile, save_profile
from app.modules.knowledge.domain.releases import (
    KnowledgeSnapshot,
    entry_hash,
    find_item,
    render_knowledge,
)
from app.modules.knowledge.infrastructure.releases import (
    active_snapshot,
    current_release_id,
    publish_snapshot,
)
from app.modules.learning.domain.candidates import strip_pii
from app.modules.learning.domain.eval_gate import (
    EVAL_SYSTEM,
    STANDARD_EVAL_SYSTEM,
    CaseOutcome,
    EvalCaseSpec,
    EvalDraftOut,
    apply_candidate_to_snapshot,
    build_lesson_case,
    build_standard_cases,
    eval_user_turn,
    evaluate_gate,
    grade_reply,
    grade_standard_reply,
    regressed_intents,
    select_regression_cases,
    still_valid,
)
from app.modules.learning.infrastructure.checks import verify_candidate
from app.modules.orders.domain.order_flow import FIELDS, OrderFlowConfig
from app.modules.orders.infrastructure.repository import current_flow, publish_flow
from app.modules.outbound.infrastructure.monitoring import demote_intent
from app.modules.shadow.domain.readiness import intent_priority_score
from app.shared.domain.text import extract_amounts, fold
from app.shared.infrastructure.signals import record_signal

log = logging.getLogger(__name__)
EVAL_DEADLINE_S = 60
MAX_EVAL_ATTEMPTS = 3  # lỗi LLM liên tiếp trước khi đánh dấu eval_failed
EVAL_CALL_RETRIES = 1  # thử lại một lời gọi eval lỗi tạm thời trước khi bỏ lượt
# ponytail: chạy lại tối đa 10 ca bài học cũ (cùng ý định trước, mới nhất trước), không phải mọi
# ca: số ca tăng theo mỗi bài học. Ca bị bỏ qua vẫn chạy khi bài học cùng ý định tới.
MAX_LESSON_CASES = 10


class CandidateBlocked(Exception):
    """Ứng viên không đạt kiểm tra sự thật / PII / chống lẫn chữ khách ⇒ không được duyệt."""

    def __init__(self, checks: dict) -> None:
        self.checks = checks
        super().__init__("candidate failed safety checks")


async def _current_entry(s: AsyncSession, tenant_id: UUID, kind: str, replaces: UUID | None):
    if replaces is None:
        return None
    if kind == "doc_section":
        row = (
            await s.execute(
                text(
                    "SELECT id, title AS first, body AS second FROM knowledge_docs "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": replaces},
            )
        ).first()
        return {"id": str(row.id), "title": row.first, "body": row.second} if row else None
    row = (
        await s.execute(
            text(
                "SELECT id, question AS first, answer AS second FROM faq_entries "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {"t": tenant_id, "id": replaces},
        )
    ).first()
    return {"id": str(row.id), "question": row.first, "answer": row.second} if row else None


def _row_json(row, current_entry: dict | None = None) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "intent": row.intent,
        "topic": row.topic,
        "proposal": row.proposal,
        "replaces": str(row.replaces) if row.replaces else None,
        "current_entry": current_entry,
        "evidence": row.evidence,
        "checks": row.checks,
        "status": row.status,
        "reject_reason": row.reject_reason,
        "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "release_id": str(row.release_id) if row.release_id else None,
        "eval_report": row.eval_report,
        "created_at": row.created_at.isoformat(),
    }


async def _intent_priorities(s: AsyncSession, tenant_id: UUID) -> dict[str, float]:
    """Tính điểm ưu tiên theo ý định từ tỉ lệ sai/bỏ 14 ngày gần nhất (thiết kế 05 §7)."""
    rows = (
        await s.execute(
            text(
                "SELECT r.intents[1] AS intent, "
                "  count(*) AS total, "
                "  count(*) FILTER (WHERE u.action = 'discarded') AS discarded, "
                "  count(*) FILTER ("
                "    WHERE u.action = 'edited' AND ("
                "      jsonb_array_length(coalesce(u.diff_features->'amounts_added','[]')) > 0 "
                "      OR jsonb_array_length("
                "        coalesce(u.diff_features->'amounts_removed','[]')) > 0"
                "    )"
                "  ) AS wrong "
                "FROM draft_uses u "
                "JOIN agent_drafts d ON d.tenant_id = u.tenant_id AND d.id = u.draft_id "
                "JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                "WHERE u.tenant_id = :t AND u.created_at > now() - interval '14 days' "
                "  AND r.intents IS NOT NULL AND array_length(r.intents, 1) >= 1 "
                "GROUP BY 1"
            ),
            {"t": tenant_id},
        )
    ).all()
    return {
        r.intent: intent_priority_score(
            r.wrong / r.total if r.total else 0.0,
            r.discarded / r.total if r.total else 0.0,
        )
        for r in rows
        if r.intent
    }


async def list_candidates(
    s: AsyncSession, tenant_id: UUID, *, status: str | None = None, limit: int = 50
) -> list[dict]:
    cond = "AND status = :st" if status else ""
    params: dict = {"t": tenant_id, "lim": limit}
    if status:
        params["st"] = status
    rows = (
        await s.execute(
            text(
                "SELECT id, kind, intent, topic, proposal, replaces, evidence, checks, status, "
                "       reject_reason, reviewed_by, reviewed_at, release_id, eval_report, "
                "       created_at "
                f"FROM knowledge_candidates WHERE tenant_id = :t {cond} "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    priorities = await _intent_priorities(s, tenant_id)
    items: list[dict] = []
    for r in rows:
        cur = await _current_entry(s, tenant_id, r.kind, r.replaces)
        items.append(_row_json(r, cur) | {"priority_score": priorities.get(r.intent, 0.0)})
    if status in (None, "pending", "eval_failed"):
        items.sort(key=lambda x: (-x["priority_score"], x["created_at"]), reverse=False)
        items.sort(key=lambda x: x["priority_score"], reverse=True)
    return items


async def approve_candidate(
    s: AsyncSession,
    tenant_id: UUID,
    candidate_id: UUID,
    *,
    reviewer_id: UUID,
    edited_proposal: dict | None = None,
) -> dict | None:
    row = (
        await s.execute(
            text(
                "SELECT id, kind, intent, topic, proposal, replaces, evidence, checks, status, "
                "       reject_reason, reviewed_by, reviewed_at, release_id, eval_report, "
                "       created_at "
                "FROM knowledge_candidates "
                "WHERE tenant_id = :t AND id = :id AND status IN ('pending','eval_failed') "
                "FOR UPDATE"
            ),
            {"t": tenant_id, "id": candidate_id},
        )
    ).first()
    if row is None:
        return None

    proposal = dict(row.proposal)
    checks_dict = dict(row.checks or {})
    if edited_proposal is not None:
        title_key = "title" if row.kind == "doc_section" else "question"
        body_key = "body" if row.kind == "doc_section" else "answer"
        raw_title = str(edited_proposal.get(title_key) or proposal.get(title_key) or "")
        raw_body = str(edited_proposal.get(body_key) or proposal.get(body_key) or "")
        clean_title, _ = strip_pii(raw_title)
        clean_body, _ = strip_pii(raw_body)
        proposal = {title_key: clean_title, body_key: clean_body}

        msg_ids = [UUID(e["message_id"]) for e in (row.evidence or []) if e.get("message_id")]
        conv_ids = [
            UUID(e["conversation_id"]) for e in (row.evidence or []) if e.get("conversation_id")
        ]
        staff_texts: list[str] = []
        if msg_ids:
            mrows = await s.execute(
                text(
                    "SELECT content FROM messages "
                    "WHERE tenant_id = :t AND id = ANY(:ids) AND sender_type = 'human' "
                    "  AND content IS NOT NULL"
                ),
                {"t": tenant_id, "ids": msg_ids},
            )
            staff_texts = [r[0] for r in mrows]
        checks = await verify_candidate(
            s,
            tenant_id,
            conv_ids,
            proposal_text=f"{clean_title}\n{clean_body}",
            staff_texts=staff_texts,
        )
        checks_dict = checks.as_dict()

    if not checks_dict.get("passed"):
        raise CandidateBlocked(checks_dict)

    next_status = "approved"
    if row.kind == "brand_example":
        sample = str(proposal.get("body") or proposal.get("answer") or "").strip()
        prof = await current_profile(s, tenant_id)
        base_profile = prof[1] if prof else BrandProfile()
        existing = {fold(x) for x in base_profile.samples}
        if sample and fold(sample) not in existing and len(base_profile.samples) < 10:
            updated_profile = base_profile.model_copy(
                update={"samples": [*base_profile.samples, sample]}
            )
            await save_profile(s, tenant_id, updated_profile, created_by=reviewer_id)
        next_status = "published"
    elif row.kind == "order_flow":
        field_name = str(proposal.get("field") or row.topic or "").strip()
        flow = await current_flow(s, tenant_id)
        base_cfg = flow.config if flow else OrderFlowConfig()
        if field_name in FIELDS and field_name not in base_cfg.required_fields:
            new_required = [f for f in FIELDS if f in base_cfg.required_fields or f == field_name]
            updated_cfg = base_cfg.model_copy(update={"required_fields": new_required})
            await publish_flow(s, tenant_id, updated_cfg, reviewer_id)
        next_status = "published"

    updated = (
        await s.execute(
            text(
                "UPDATE knowledge_candidates "
                "SET status = :st, proposal = CAST(:prop AS jsonb), "
                "    checks = CAST(:chk AS jsonb), reviewed_by = :by, reviewed_at = now(), "
                "    eval_report = NULL, updated_at = now() "
                "WHERE tenant_id = :t AND id = :id "
                "RETURNING id, kind, intent, topic, proposal, replaces, evidence, checks, status, "
                "          reject_reason, reviewed_by, reviewed_at, release_id, eval_report, "
                "          created_at"
            ),
            {
                "st": next_status,
                "t": tenant_id,
                "id": candidate_id,
                "prop": json.dumps(proposal, ensure_ascii=False),
                "chk": json.dumps(checks_dict, ensure_ascii=False),
                "by": reviewer_id,
            },
        )
    ).one()
    await record_signal(
        s,
        tenant_id,
        None,
        "candidate_approved",
        {
            "candidate_id": str(candidate_id),
            "kind": updated.kind,
            "edited": edited_proposal is not None,
        },
    )
    return _row_json(updated, await _current_entry(s, tenant_id, updated.kind, updated.replaces))


async def reject_candidate(
    s: AsyncSession,
    tenant_id: UUID,
    candidate_id: UUID,
    *,
    reviewer_id: UUID,
    reason: str | None = None,
) -> dict | None:
    updated = (
        await s.execute(
            text(
                "UPDATE knowledge_candidates "
                "SET status = 'rejected', reject_reason = :r, reviewed_by = :by, "
                "    reviewed_at = now(), updated_at = now() "
                "WHERE tenant_id = :t AND id = :id AND status IN ('pending','eval_failed') "
                "RETURNING id, kind, intent, topic, proposal, replaces, evidence, checks, status, "
                "          reject_reason, reviewed_by, reviewed_at, release_id, eval_report, "
                "          created_at"
            ),
            {"t": tenant_id, "id": candidate_id, "r": reason, "by": reviewer_id},
        )
    ).first()
    if updated is None:
        return None
    await record_signal(
        s,
        tenant_id,
        None,
        "candidate_rejected",
        {"candidate_id": str(candidate_id), "kind": updated.kind},
    )
    return _row_json(updated, await _current_entry(s, tenant_id, updated.kind, updated.replaces))


async def _sync_entry_table(
    s: AsyncSession,
    tenant_id: UUID,
    *,
    kind: str,
    proposal: dict,
    replaces: UUID | None,
    item_id: UUID,
    topic: str | None,
) -> None:
    """Giữ bảng soạn thảo FAQ/tài liệu đồng bộ với bản phát hành mới từ bài học."""
    topics = [topic] if topic and topic != "other" else None
    if kind == "doc_section":
        title = str(proposal.get("title") or proposal.get("question") or "")
        body = str(proposal.get("body") or proposal.get("answer") or "")
        if replaces is not None:
            await s.execute(
                text(
                    "UPDATE knowledge_docs SET title = :a, body = :b, status = 'approved', "
                    "updated_at = now() WHERE tenant_id = :t AND id = :id"
                ),
                {"a": title, "b": body, "t": tenant_id, "id": replaces},
            )
        else:
            await s.execute(
                text(
                    "INSERT INTO knowledge_docs (id, tenant_id, title, body, status, topics) "
                    "VALUES (:id, :t, :a, :b, 'approved', :tp)"
                ),
                {"id": item_id, "t": tenant_id, "a": title, "b": body, "tp": topics},
            )
        return

    question = str(proposal.get("question") or proposal.get("title") or "")
    answer = str(proposal.get("answer") or proposal.get("body") or "")
    if replaces is not None:
        await s.execute(
            text(
                "UPDATE faq_entries SET question = :a, answer = :b, status = 'approved', "
                "updated_at = now() WHERE tenant_id = :t AND id = :id"
            ),
            {"a": question, "b": answer, "t": tenant_id, "id": replaces},
        )
    else:
        await s.execute(
            text(
                "INSERT INTO faq_entries (id, tenant_id, question, answer, status, topics) "
                "VALUES (:id, :t, :a, :b, 'approved', :tp)"
            ),
            {"id": item_id, "t": tenant_id, "a": question, "b": answer, "tp": topics},
        )


async def _active_autopilot_intents(s: AsyncSession, tenant_id: UUID) -> list[tuple[UUID, str]]:
    """Danh sách `(agent_id, intent)` đang ở trạng thái `autopilot` của tenant."""
    rows = (
        await s.execute(
            text(
                "SELECT agent_id, intent FROM autopilot_intents "
                "WHERE tenant_id = :t AND state = 'autopilot' ORDER BY agent_id, intent"
            ),
            {"t": tenant_id},
        )
    ).all()
    return [(r.agent_id, r.intent) for r in rows]


CATALOG_EVAL_LIMIT = 30  # ponytail: chỉ 30 sản phẩm đầu theo tên; đủ cho 2 sản phẩm được hỏi


async def _catalog_eval_context(
    s: AsyncSession, tenant_id: UUID
) -> tuple[set[int], list[str], set[tuple[str, str]], str]:
    """Giá, tên, nguồn và khối `<catalog>` của sản phẩm đang bán cho ca chuẩn.

    Bot thật tra sản phẩm bằng tool; model eval không có tool, nên phải thấy catalog trong prompt.
    Không có khối này, mọi ca `product_info`/`price_quote` đều thành "chuyển người" và trượt.
    """
    rows = (
        await s.execute(
            text(
                "SELECT id, name, price_vnd FROM catalog_items "
                "WHERE tenant_id = :t AND active ORDER BY name"
            ),
            {"t": tenant_id},
        )
    ).all()
    rows = rows[:CATALOG_EVAL_LIMIT]
    prices = {int(r.price_vnd) for r in rows if r.price_vnd is not None}
    names = [r.name for r in rows if r.name]
    sources = {("product", str(r.id)) for r in rows}
    lines = [
        f'<product id="{r.id}" price_vnd="{r.price_vnd}">{escape(r.name)}</product>' for r in rows
    ]
    block = "\n".join(["<catalog>", *lines, "</catalog>"]) if rows else ""
    return prices, names, sources, block


async def _load_active_eval_cases(s: AsyncSession, tenant_id: UUID) -> list[EvalCaseSpec]:
    rows = (
        await s.execute(
            text(
                "SELECT id, source, intent, question, expected, entry_id, entry_hash "
                "FROM eval_cases WHERE tenant_id = :t AND active ORDER BY created_at"
            ),
            {"t": tenant_id},
        )
    ).all()
    return [
        EvalCaseSpec.from_row(
            str(r.id),
            r.question,
            r.expected or {},
            entry_id=str(r.entry_id) if r.entry_id else None,
            entry_hash=r.entry_hash,
            source=r.source,
            intent=r.intent,
        )
        for r in rows
    ]


async def _execute_specs(
    llm: LLMPort,
    rendered_block: str,
    specs: list[EvalCaseSpec],
    *,
    catalog_block: str = "",
    valid_sources: set[tuple[str, str]],
    known_amounts: set[int],
    clock: Callable[[], float],
    log_context: dict,
) -> tuple[list[CaseOutcome], list, bool]:
    outcomes: list[CaseOutcome] = []
    usage_results = []
    standard_block = "\n".join(filter(None, (rendered_block, catalog_block)))
    for spec in specs:
        behavior = spec.check_behavior
        system = STANDARD_EVAL_SYSTEM if behavior else EVAL_SYSTEM
        turn = eval_user_turn(standard_block if behavior else rendered_block, spec.question)
        res = None
        for attempt in range(1 + EVAL_CALL_RETRIES):
            try:
                res = await llm.complete(
                    LLMRequest(
                        route=LEARNING,
                        system_blocks=(system,),
                        turns=(Turn(role="user", text=turn),),
                        output_schema=EvalDraftOut if behavior else None,
                        deadline=clock() + EVAL_DEADLINE_S,
                    )
                )
                break
            except (AllTargetsFailed, ProviderUnavailable, DeadlineExceeded, InvalidOutput) as exc:
                # Lỗi tạm thời của một lời gọi: thử lại chính lời gọi đó, không bỏ cả lượt eval
                # (bỏ cả lượt thì tốn lại tiền mọi ca đã chạy, và hay hỏng khi có nhiều ca).
                log.warning(
                    "learning eval gate call failed",
                    extra=log_context | {"error": type(exc).__name__, "attempt": attempt + 1},
                )
            except (BadRequest, UnknownPrice) as exc:  # lỗi cấu hình: thử lại vô ích
                log.warning(
                    "learning eval gate call failed",
                    extra=log_context | {"error": type(exc).__name__},
                )
                break
        if res is None:
            return outcomes, usage_results, True
        usage_results.append(res)
        if spec.check_behavior:
            draft = (
                res.parsed
                if isinstance(res.parsed, EvalDraftOut)
                else EvalDraftOut(reply=res.text or "")
            )
            outcomes.append(
                grade_standard_reply(
                    spec,
                    draft,
                    valid_sources=valid_sources,
                    known_amounts=known_amounts,
                )
            )
        else:
            outcomes.append(grade_reply(spec, res.text or ""))
    return outcomes, usage_results, False


async def run_eval_gate_for_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMPort,
    *,
    tenant_id: UUID,
    candidate_id: UUID,
    daily_cap_micro: int,
    clock: Callable[[], float] = time.monotonic,
) -> dict | None:
    """Chạy cổng eval cho một ứng viên `approved`. Trả báo cáo eval, hoặc None nếu chưa chạy."""
    async with session_factory() as s:
        cand = (
            await s.execute(
                text(
                    "SELECT id, kind, intent, topic, proposal, replaces, reviewed_by, status "
                    "FROM knowledge_candidates WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": candidate_id},
            )
        ).first()
        if cand is None or cand.status != "approved":
            return None
        if await spent_today_micro(s, tenant_id, purpose=LEARNING) >= daily_cap_micro:
            return None
        base_release = await current_release_id(s, tenant_id)
        snapshot = await active_snapshot(s, tenant_id)
        old_specs = await _load_active_eval_cases(s, tenant_id)
        autopilot_rows = await _active_autopilot_intents(s, tenant_id)
        catalog_prices: set[int] = set()
        catalog_names: list[str] = []
        catalog_sources: set[tuple[str, str]] = set()
        catalog_block = ""
        if autopilot_rows:
            (
                catalog_prices,
                catalog_names,
                catalog_sources,
                catalog_block,
            ) = await _catalog_eval_context(s, tenant_id)

    old_answer: str | None = None
    if cand.replaces:
        rep_str = str(cand.replaces)
        for item in (*snapshot.faqs, *snapshot.docs):
            if item.id == rep_str:
                old_answer = item.second
                break

    new_item_id = uuid4()
    draft_snapshot = apply_candidate_to_snapshot(
        snapshot,
        kind=cand.kind,
        proposal=cand.proposal,
        replaces=str(cand.replaces) if cand.replaces else None,
        item_id=str(new_item_id),
        topic=cand.topic,
    )
    rendered = render_knowledge(draft_snapshot)
    new_case_id = uuid4()
    new_spec = build_lesson_case(
        cand.proposal,
        kind=cand.kind,
        case_id=str(new_case_id),
        old_answer=old_answer,
        intent=cand.intent,
    )
    target_id = str(cand.replaces) if cand.replaces else str(new_item_id)
    target = find_item(draft_snapshot, target_id)

    active_intent_set = {intent for _, intent in autopilot_rows}
    # Ca của bài học cũ cho đúng mục này (hay mục chủ shop đã sửa) không còn hiệu lực: không chạy,
    # và tắt đi khi phát hành. Ca chuẩn lưu sẵn chỉ chạy cho ý định đang Autopilot.
    kept: list[EvalCaseSpec] = []
    lesson_cases: list[EvalCaseSpec] = []
    retired: list[str] = []
    covered_std_entries: set[str] = set()
    for c in old_specs:
        if not still_valid(c, draft_snapshot):
            retired.append(c.case_id)
            continue
        if c.source == "standard":
            if c.intent in active_intent_set:
                kept.append(c)
                if c.entry_id:
                    covered_std_entries.add(c.entry_id)
        else:
            lesson_cases.append(c)
    kept += select_regression_cases(lesson_cases, intent=cand.intent, limit=MAX_LESSON_CASES)

    standard_specs = (
        build_standard_cases(
            sorted(active_intent_set),
            draft_snapshot,
            catalog_names=catalog_names,
            covered_entry_ids=frozenset(covered_std_entries),
        )
        if active_intent_set
        else []
    )
    specs = [new_spec, *kept, *standard_specs]
    valid_sources = set(rendered.sources) | catalog_sources
    known_amounts = extract_amounts(rendered.text) | catalog_prices

    outcomes, usage_results, failed_call = await _execute_specs(
        llm,
        rendered.block,
        specs,
        catalog_block=catalog_block,
        valid_sources=valid_sources,
        known_amounts=known_amounts,
        clock=clock,
        log_context={"candidate_id": str(candidate_id)},
    )

    if failed_call:
        # Ghi chi phí các lời gọi đã chạy (để trần chi phí đúng), rồi thử lại có giới hạn.
        async with session_factory() as s, s.begin():
            for res in usage_results:
                await _record_usage_safe(s, tenant_id, res)
            await s.execute(
                text(
                    "UPDATE knowledge_candidates SET eval_attempts = eval_attempts + 1, "
                    "status = CASE WHEN eval_attempts + 1 >= :max THEN 'eval_failed' "
                    "ELSE status END, "
                    "eval_report = CASE WHEN eval_attempts + 1 >= :max "
                    "THEN CAST(:rep AS jsonb) ELSE eval_report END, updated_at = now() "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "max": MAX_EVAL_ATTEMPTS,
                    "rep": json.dumps({"passed": False, "error": "llm_unavailable"}),
                    "t": tenant_id,
                    "id": candidate_id,
                },
            )
        return None

    decision = evaluate_gate(outcomes)
    eval_run_id = uuid4()
    report = decision.as_dict() | {"eval_run_id": str(eval_run_id)}

    async with session_factory() as s, s.begin():
        for res in usage_results:
            await _record_usage_safe(s, tenant_id, res)
        # Khóa tenant trước khi so con trỏ (publish_snapshot cũng khóa đúng dòng này).
        await s.execute(text("SELECT 1 FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id})
        if decision.passed and await current_release_id(s, tenant_id) != base_release:
            # Tri thức đã đổi trong lúc chạy eval (chủ shop sửa FAQ, bài học khác): không phát
            # hành bản dựng trên ảnh chụp cũ, kẻo đè mất thay đổi đó. Lượt sau chạy lại.
            log.info("knowledge changed during eval", extra={"candidate_id": str(candidate_id)})
            return None
        if decision.passed:
            await _sync_entry_table(
                s,
                tenant_id,
                kind=cand.kind,
                proposal=cand.proposal,
                replaces=cand.replaces,
                item_id=new_item_id,
                topic=cand.topic,
            )
            release = await publish_snapshot(
                s,
                tenant_id,
                draft_snapshot,
                reason="lesson",
                created_by=cand.reviewed_by,
                eval_run_id=eval_run_id,
                eval_report=report,
            )
            await s.execute(
                text(
                    "INSERT INTO eval_cases (id, tenant_id, source, intent, candidate_id, "
                    "question, expected, active, entry_id, entry_hash) "
                    "VALUES (:id, :t, 'lesson', :intent, :cid, :q, CAST(:exp AS jsonb), "
                    "true, :eid, :eh)"
                ),
                {
                    "id": new_case_id,
                    "t": tenant_id,
                    "intent": cand.intent,
                    "cid": candidate_id,
                    "q": new_spec.question,
                    "exp": json.dumps(new_spec.expected_dict(), ensure_ascii=False),
                    "eid": UUID(target_id),
                    "eh": entry_hash(target) if target else None,
                },
            )
            if retired:
                await s.execute(
                    text(
                        "UPDATE eval_cases SET active = false "
                        "WHERE tenant_id = :t AND id = ANY(:ids)"
                    ),
                    {"t": tenant_id, "ids": [UUID(c) for c in retired]},
                )
            await s.execute(
                text(
                    "UPDATE knowledge_candidates "
                    "SET status = 'published', release_id = :rel, "
                    "    eval_report = CAST(:rep AS jsonb), updated_at = now() "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "t": tenant_id,
                    "id": candidate_id,
                    "rel": release.id,
                    "rep": json.dumps(report, ensure_ascii=False),
                },
            )
        else:
            await s.execute(
                text(
                    "UPDATE knowledge_candidates "
                    "SET status = 'eval_failed', eval_report = CAST(:rep AS jsonb), "
                    "    updated_at = now() "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": candidate_id, "rep": json.dumps(report, ensure_ascii=False)},
            )
    return report


async def run_eval_for_release(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMPort,
    *,
    tenant_id: UUID,
    release_id: UUID,
    daily_cap_micro: int,
    clock: Callable[[], float] = time.monotonic,
) -> dict | None:
    """Chạy bộ ca chuẩn cho bản phát hành thủ công khi tenant có ý định Autopilot (06 §6.3, A5).

    Bản sửa tay của chủ shop LUÔN giữ hiệu lực (chủ shop là nguồn sự thật); chỉ những ý định có
    ca chuẩn bị hỏng mới tự hạ cấp về Learning kèm `demote_reason = 'eval_regressed'`.
    """
    async with session_factory() as s:
        rel = (
            await s.execute(
                text(
                    "SELECT r.id, r.snapshot, r.eval_run_id, r.eval_attempts, "
                    "       tk.current_release_id "
                    "FROM knowledge_releases r "
                    "JOIN tenant_knowledge tk ON tk.tenant_id = r.tenant_id "
                    "WHERE r.tenant_id = :t AND r.id = :id"
                ),
                {"t": tenant_id, "id": release_id},
            )
        ).first()
        if (
            rel is None
            or rel.current_release_id != release_id
            or rel.eval_run_id is not None
            or rel.eval_attempts >= MAX_EVAL_ATTEMPTS
        ):
            return None
        autopilot_rows = await _active_autopilot_intents(s, tenant_id)
        if not autopilot_rows:
            return None
        if await spent_today_micro(s, tenant_id, purpose=LEARNING) >= daily_cap_micro:
            return None
        old_specs = await _load_active_eval_cases(s, tenant_id)
        (
            catalog_prices,
            catalog_names,
            catalog_sources,
            catalog_block,
        ) = await _catalog_eval_context(s, tenant_id)

    snapshot = KnowledgeSnapshot.from_dict(rel.snapshot)
    rendered = render_knowledge(snapshot)
    active_intent_set = {intent for _, intent in autopilot_rows}

    kept_std: list[EvalCaseSpec] = []
    retired: list[str] = []
    covered_std_entries: set[str] = set()
    for c in old_specs:
        if c.source != "standard":
            continue
        if not still_valid(c, snapshot):
            retired.append(c.case_id)
            continue
        if c.intent in active_intent_set:
            kept_std.append(c)
            if c.entry_id:
                covered_std_entries.add(c.entry_id)

    dynamic_std = build_standard_cases(
        sorted(active_intent_set),
        snapshot,
        catalog_names=catalog_names,
        covered_entry_ids=frozenset(covered_std_entries),
    )
    specs = [*kept_std, *dynamic_std]
    if not specs:
        return None

    valid_sources = set(rendered.sources) | catalog_sources
    known_amounts = extract_amounts(rendered.text) | catalog_prices
    outcomes, usage_results, failed_call = await _execute_specs(
        llm,
        rendered.block,
        specs,
        catalog_block=catalog_block,
        valid_sources=valid_sources,
        known_amounts=known_amounts,
        clock=clock,
        log_context={"release_id": str(release_id)},
    )

    if failed_call:
        async with session_factory() as s, s.begin():
            for res in usage_results:
                await _record_usage_safe(s, tenant_id, res)
            await s.execute(
                text(
                    "UPDATE knowledge_releases SET eval_attempts = eval_attempts + 1, "
                    "eval_report = CASE WHEN eval_attempts + 1 >= :max "
                    "THEN CAST(:rep AS jsonb) ELSE eval_report END "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "max": MAX_EVAL_ATTEMPTS,
                    "rep": json.dumps({"passed": False, "error": "llm_unavailable"}),
                    "t": tenant_id,
                    "id": release_id,
                },
            )
        return None

    decision = evaluate_gate(outcomes)
    bad_intents = regressed_intents(outcomes)
    eval_run_id = uuid4()
    demoted: list[str] = []

    async with session_factory() as s, s.begin():
        for res in usage_results:
            await _record_usage_safe(s, tenant_id, res)
        await s.execute(text("SELECT 1 FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id})
        still_current = await current_release_id(s, tenant_id) == release_id
        if still_current:
            if retired:
                await s.execute(
                    text(
                        "UPDATE eval_cases SET active = false "
                        "WHERE tenant_id = :t AND id = ANY(:ids)"
                    ),
                    {"t": tenant_id, "ids": [UUID(c) for c in retired]},
                )
            for agent_id, intent in autopilot_rows:
                if intent in bad_intents and await demote_intent(
                    s, tenant_id, agent_id, intent, reason="eval_regressed"
                ):
                    demoted.append(intent)
        report = decision.as_dict() | {
            "eval_run_id": str(eval_run_id),
            "demoted_intents": sorted(set(demoted)),
        }
        await s.execute(
            text(
                "UPDATE knowledge_releases "
                "SET eval_run_id = :ev, eval_report = CAST(:rep AS jsonb) "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {
                "ev": eval_run_id,
                "rep": json.dumps(report, ensure_ascii=False),
                "t": tenant_id,
                "id": release_id,
            },
        )
    return report


async def evaluate_manual_releases(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMPort,
    *,
    daily_cap_micro: int,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Chạy bộ ca chuẩn cho các bản phát hành hiện hành chưa có `eval_run_id` ở tenant Autopilot."""
    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT tk.tenant_id, r.id AS release_id "
                    "FROM tenant_knowledge tk "
                    "JOIN knowledge_releases r "
                    "  ON r.tenant_id = tk.tenant_id AND r.id = tk.current_release_id "
                    "WHERE r.eval_run_id IS NULL "
                    "  AND r.eval_attempts < :max "
                    "  AND EXISTS ("
                    "    SELECT 1 FROM autopilot_intents ai "
                    "    WHERE ai.tenant_id = tk.tenant_id AND ai.state = 'autopilot'"
                    "  ) "
                    "ORDER BY r.created_at ASC LIMIT 20"
                ),
                {"max": MAX_EVAL_ATTEMPTS},
            )
        ).all()
    evaluated = 0
    for tenant_id, release_id in rows:
        report = await run_eval_for_release(
            session_factory,
            llm,
            tenant_id=tenant_id,
            release_id=release_id,
            daily_cap_micro=daily_cap_micro,
            clock=clock,
        )
        if report is not None:
            evaluated += 1
    return evaluated


async def _record_usage_safe(s: AsyncSession, tenant_id: UUID, res) -> None:
    try:
        await record_usage(s, tenant_id=tenant_id, run_id=None, route=LEARNING, result=res)
    except UnknownPrice:  # cấu hình route sai: phải thấy trong log, không làm hỏng cổng eval
        log.error("learning model has no price", extra={"model": res.served_by.model})


async def process_approved_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMPort,
    *,
    daily_cap_micro: int,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT tenant_id, id FROM knowledge_candidates "
                    "WHERE status = 'approved' AND eval_attempts < :max "
                    "ORDER BY reviewed_at ASC LIMIT 20"
                ),
                {"max": MAX_EVAL_ATTEMPTS},
            )
        ).all()
    processed = 0
    for tenant_id, candidate_id in rows:
        report = await run_eval_gate_for_candidate(
            session_factory,
            llm,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            daily_cap_micro=daily_cap_micro,
            clock=clock,
        )
        if report is not None:
            processed += 1
    return processed
