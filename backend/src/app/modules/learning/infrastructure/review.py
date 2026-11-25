"""Duyệt/bỏ ứng viên tri thức và cổng eval hồi quy trước khi phát hành (thiết kế 05 L4).

Chủ shop duyệt (hoặc sửa rồi duyệt) ⇒ `approved` ⇒ cổng eval chạy ca hồi quy mới + các ca hồi quy
cũ trên bản nháp phát hành ⇒ đạt mới chuyển con trỏ `tenant_knowledge` (`published`), hỏng thì
`eval_failed` kèm ca lỗi để chủ shop sửa lại.
"""

import json
import logging
import time
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
from app.modules.knowledge.domain.releases import render_knowledge
from app.modules.knowledge.infrastructure.releases import active_snapshot, publish_snapshot
from app.modules.learning.domain.candidates import strip_pii
from app.modules.learning.domain.eval_gate import (
    EVAL_SYSTEM,
    EvalCaseSpec,
    apply_candidate_to_snapshot,
    build_lesson_case,
    eval_user_turn,
    evaluate_gate,
    grade_reply,
)
from app.modules.learning.infrastructure.checks import verify_candidate
from app.modules.shadow.domain.readiness import intent_priority_score
from app.shared.infrastructure.signals import record_signal

log = logging.getLogger(__name__)
EVAL_DEADLINE_S = 60


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

    updated = (
        await s.execute(
            text(
                "UPDATE knowledge_candidates "
                "SET status = 'approved', proposal = CAST(:prop AS jsonb), "
                "    checks = CAST(:chk AS jsonb), reviewed_by = :by, reviewed_at = now(), "
                "    eval_report = NULL, updated_at = now() "
                "WHERE tenant_id = :t AND id = :id "
                "RETURNING id, kind, intent, topic, proposal, replaces, evidence, checks, status, "
                "          reject_reason, reviewed_by, reviewed_at, release_id, eval_report, "
                "          created_at"
            ),
            {
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
        snapshot = await active_snapshot(s, tenant_id)
        existing_cases = (
            await s.execute(
                text(
                    "SELECT id, question, expected FROM eval_cases "
                    "WHERE tenant_id = :t AND active ORDER BY created_at"
                ),
                {"t": tenant_id},
            )
        ).all()

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
        cand.proposal, kind=cand.kind, case_id=str(new_case_id), old_answer=old_answer
    )
    specs = [new_spec] + [
        EvalCaseSpec.from_row(str(r.id), r.question, r.expected or {}) for r in existing_cases
    ]

    outcomes = []
    usage_results = []
    for spec in specs:
        try:
            res = await llm.complete(
                LLMRequest(
                    route=LEARNING,
                    system_blocks=(EVAL_SYSTEM,),
                    turns=(Turn(role="user", text=eval_user_turn(rendered.block, spec.question)),),
                    deadline=clock() + EVAL_DEADLINE_S,
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
                "learning eval gate call failed",
                extra={"candidate_id": str(candidate_id), "error": type(exc).__name__},
            )
            return None
        usage_results.append(res)
        outcomes.append(grade_reply(spec, res.text or ""))

    decision = evaluate_gate(outcomes)
    eval_run_id = uuid4()
    report = decision.as_dict() | {"eval_run_id": str(eval_run_id)}

    async with session_factory() as s, s.begin():
        for res in usage_results:
            await record_usage(s, tenant_id=tenant_id, run_id=None, route=LEARNING, result=res)
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
            )
            await s.execute(
                text(
                    "INSERT INTO eval_cases "
                    "(id, tenant_id, source, candidate_id, question, expected, active) "
                    "VALUES (:id, :t, 'lesson', :cid, :q, CAST(:exp AS jsonb), true)"
                ),
                {
                    "id": new_case_id,
                    "t": tenant_id,
                    "cid": candidate_id,
                    "q": new_spec.question,
                    "exp": json.dumps(new_spec.expected_dict(), ensure_ascii=False),
                },
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
                    "WHERE status = 'approved' ORDER BY reviewed_at ASC LIMIT 20"
                )
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
