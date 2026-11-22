"""Read model của trace view: SQL thẳng, mọi truy vấn lọc tenant_id từ session nhân viên."""

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_runtime.context import MAX_MESSAGE_CHARS


class InvalidCursor(ValueError):
    pass


def encode_cursor(at: datetime, run_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{run_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        at, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(at), UUID(rid)
    except (ValueError, UnicodeError) as exc:
        raise InvalidCursor from exc


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


_LIST = (
    "SELECT r.id, r.conversation_id, r.status, r.skip_reason, r.error_code, r.intents, "
    "r.model_calls, r.tool_calls, r.cost_micro, r.started_at, r.finished_at, "
    "d.grounded, d.needs_human "
    "FROM agent_runs r LEFT JOIN agent_drafts d ON d.tenant_id = r.tenant_id AND d.run_id = r.id "
    "WHERE r.tenant_id = :t "
)


async def list_runs(
    s: AsyncSession,
    tenant_id: UUID,
    *,
    conversation_id: UUID | None,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    # Các mệnh đề dưới đây là hằng của file này; giá trị luôn đi qua tham số ràng buộc.
    sql, params = _LIST, {"t": tenant_id, "n": limit + 1}
    if conversation_id:
        sql, params["c"] = sql + "AND r.conversation_id = :c ", conversation_id
    if status:
        sql, params["st"] = sql + "AND r.status = :st ", status
    if cursor:
        at, rid = decode_cursor(cursor)
        sql += "AND (r.started_at, r.id) < (:at, :rid) "
        params |= {"at": at, "rid": rid}
    rows = (
        await s.execute(text(sql + "ORDER BY r.started_at DESC, r.id DESC LIMIT :n"), params)
    ).all()
    page = rows[:limit]
    items = [
        {
            "id": str(r.id),
            "conversation_id": str(r.conversation_id),
            "status": r.status,
            "skip_reason": r.skip_reason,
            "error_code": r.error_code,
            "intents": r.intents or [],
            "model_calls": r.model_calls,
            "tool_calls": r.tool_calls,
            "cost_micro": r.cost_micro,
            "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at),
            "grounded": r.grounded,
            "needs_human": r.needs_human,
        }
        for r in page
    ]
    more = len(rows) > limit
    return items, encode_cursor(page[-1].started_at, page[-1].id) if more else None


async def get_run(s: AsyncSession, tenant_id: UUID, run_id: UUID) -> dict[str, Any] | None:
    base = {"t": tenant_id, "r": run_id}
    run = (
        (
            await s.execute(
                text(
                    "SELECT r.*, v.version_number FROM agent_runs r "
                    "JOIN agent_versions v ON v.tenant_id = r.tenant_id "
                    "  AND v.id = r.agent_version_id "
                    "WHERE r.tenant_id = :t AND r.id = :r"
                ),
                base,
            )
        )
        .mappings()
        .first()
    )
    if run is None:
        return None
    steps = (
        await s.execute(
            text(
                "SELECT step_index, kind, name, status, latency_ms, detail FROM agent_run_steps "
                "WHERE tenant_id = :t AND run_id = :r ORDER BY step_index"
            ),
            base,
        )
    ).all()
    draft = (
        (
            await s.execute(
                text(
                    "SELECT id, text, sources, grounded, needs_human, reason, based_on_sequence "
                    "FROM agent_drafts WHERE tenant_id = :t AND run_id = :r"
                ),
                base,
            )
        )
        .mappings()
        .first()
    )
    usage = (
        await s.execute(
            text(
                "SELECT purpose, provider, model, input_tokens, cache_read_tokens, "
                "cache_write_tokens, output_tokens, cost_micro, failed_over_from "
                "FROM usage_events WHERE tenant_id = :t AND run_id = :r ORDER BY id"
            ),
            base,
        )
    ).all()
    messages = (
        await s.execute(
            text(
                "SELECT sequence, sender_type, left(content, :n) AS content FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c "
                "AND sequence BETWEEN :f AND :to AND content IS NOT NULL ORDER BY sequence"
            ),
            {
                "t": tenant_id,
                "c": run["conversation_id"],
                "f": run["from_sequence"],
                "to": run["to_sequence"],
                "n": MAX_MESSAGE_CHARS,
            },
        )
    ).all()
    # Quyết định của tầng chính sách cho cùng cụm tin (2.7). SQL thẳng thay vì import module
    # chính sách: hướng phụ thuộc là chính sách → runtime, không ngược lại.
    policy = (
        (
            await s.execute(
                text(
                    "SELECT d.situation, d.canned_text, d.handling_before, d.set_handling, "
                    "d.run_agent, d.sent, r.number AS release_number FROM policy_decisions d "
                    "JOIN policy_releases r ON r.tenant_id = d.tenant_id "
                    "  AND r.id = d.policy_release_id "
                    "WHERE d.tenant_id = :t AND d.conversation_id = :c AND d.to_sequence = :q"
                ),
                {"t": tenant_id, "c": run["conversation_id"], "q": run["to_sequence"]},
            )
        )
        .mappings()
        .first()
    )
    return {
        "id": str(run["id"]),
        "conversation_id": str(run["conversation_id"]),
        "policy": dict(policy)
        if policy
        else None,  # quyết định của tầng chính sách cho cụm tin này (2.7), có thể None
        "agent_id": str(run["agent_id"]),
        "agent_version": {"id": str(run["agent_version_id"]), "number": run["version_number"]},
        "status": run["status"],
        "skip_reason": run["skip_reason"],
        "error_code": run["error_code"],
        "intents": run["intents"] or [],
        "prompt_version": run["prompt_version"],
        "knowledge_hash": run["knowledge_hash"],
        "from_sequence": run["from_sequence"],
        "to_sequence": run["to_sequence"],
        "started_at": _iso(run["started_at"]),
        "finished_at": _iso(run["finished_at"]),
        "totals": {
            "model_calls": run["model_calls"],
            "tool_calls": run["tool_calls"],
            "input_tokens": run["input_tokens"],
            "cache_read_tokens": run["cache_read_tokens"],
            "cache_write_tokens": run["cache_write_tokens"],
            "output_tokens": run["output_tokens"],
            "cost_micro": run["cost_micro"],
        },
        "input_messages": [
            {"sequence": m.sequence, "from": m.sender_type, "content": m.content} for m in messages
        ],
        "steps": [
            {
                "index": st.step_index,
                "kind": st.kind,
                "name": st.name,
                "status": st.status,
                "latency_ms": st.latency_ms,
                "detail": st.detail,
            }
            for st in steps
        ],
        "draft": None
        if draft is None
        else {
            "id": str(draft["id"]),
            "text": draft["text"],
            "sources": draft["sources"],
            "grounded": draft["grounded"],
            "needs_human": draft["needs_human"],
            "reason": draft["reason"],
            "based_on_sequence": draft["based_on_sequence"],
        },
        "usage": [
            {
                "route": u.purpose,
                "provider": u.provider,
                "model": u.model,
                "input_tokens": u.input_tokens,
                "cache_read_tokens": u.cache_read_tokens,
                "cache_write_tokens": u.cache_write_tokens,
                "output_tokens": u.output_tokens,
                "cost_micro": u.cost_micro,
                "failed_over_from": u.failed_over_from,
            }
            for u in usage
        ],
    }
