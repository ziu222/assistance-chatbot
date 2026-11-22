"""Lưu run, step và bản nháp (SQL thẳng, mọi câu lọc tenant_id)."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_management.infrastructure.templates import AgentTemplate
from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.outbox import OutboxWriter


@dataclass(frozen=True)
class AgentForRun:
    agent_id: UUID
    version_id: UUID
    mode: str
    sample_rate: Decimal
    shop_name: str
    template: AgentTemplate
    handling: str = "bot"  # bot | waiting_human | human (tầng chính sách, §12.3)


@dataclass
class Step:
    kind: str  # rule | classify | llm | tool | validate
    name: str
    status: str
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DraftToSave:
    text: str
    sources: list[dict[str, str]]
    grounded: bool
    needs_human: bool
    reason: str | None


@dataclass
class Totals:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_micro: int = 0


async def load_agent(s: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> AgentForRun | None:
    """Agent của kênh mà hội thoại thuộc về, kèm version hiện tại. None ⇒ kênh chưa có agent."""
    row = (
        await s.execute(
            text(
                "SELECT a.id, a.current_version_id, a.mode, a.shadow_sample_rate, t.name, "
                "v.config, c.handling "
                "FROM conversations c "
                "JOIN agents a ON a.tenant_id = c.tenant_id "
                "  AND a.channel_connection_id = c.channel_connection_id "
                "JOIN agent_versions v ON v.tenant_id = a.tenant_id "
                "  AND v.id = a.current_version_id "
                "JOIN tenants t ON t.id = c.tenant_id "
                "WHERE c.tenant_id = :t AND c.id = :c"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).first()
    if row is None:
        return None
    return AgentForRun(
        row[0], row[1], row[2], row[3], row[4], AgentTemplate.model_validate(row[5]), row[6]
    )


async def start_run(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    agent: AgentForRun,
    conversation_id: UUID,
    from_sequence: int,
    to_sequence: int,
    prompt_version: str,
) -> UUID:
    """Mở run `running`. Run `running` cũ của cùng hội thoại là của worker đã chết (lease hết
    hạn, hàng đợi bảo đảm một run mỗi hội thoại) ⇒ đánh `timed_out`."""
    await s.execute(
        text(
            "UPDATE agent_runs SET status = 'timed_out', finished_at = now() "
            "WHERE tenant_id = :t AND conversation_id = :c AND status = 'running'"
        ),
        {"t": tenant_id, "c": conversation_id},
    )
    run_id = uuid4()
    await s.execute(
        text(
            "INSERT INTO agent_runs (id, tenant_id, agent_id, agent_version_id, conversation_id, "
            "from_sequence, to_sequence, status, knowledge_hash, prompt_version) "
            "VALUES (:id, :t, :a, :v, :c, :f, :to, 'running', '', :pv)"
        ),
        {
            "id": run_id,
            "t": tenant_id,
            "a": agent.agent_id,
            "v": agent.version_id,
            "c": conversation_id,
            "f": from_sequence,
            "to": to_sequence,
            "pv": prompt_version,
        },
    )
    return run_id


@dataclass(frozen=True)
class SaveGuard:
    mode: str
    last_sequence: int


async def lock_for_save(s: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> SaveGuard:
    """Đọc lại mode + last_sequence và giữ khóa tới hết transaction: tin mới (UPDATE hội thoại)
    phải chờ, nên kiểm tra "chưa có tin mới" và ghi nháp là một bước nguyên tử (bất biến 3)."""
    row = (
        await s.execute(
            text(
                "SELECT a.mode, c.last_sequence FROM conversations c "
                "JOIN agents a ON a.tenant_id = c.tenant_id "
                "  AND a.channel_connection_id = c.channel_connection_id "
                "WHERE c.tenant_id = :t AND c.id = :c FOR SHARE OF c, a"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).one()
    return SaveGuard(row[0], row[1])


async def finish_run(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    run_id: UUID,
    status: str,
    skip_reason: str | None,
    error_code: str | None,
    intents: list[str] | None,
    knowledge_hash: str,
    totals: Totals,
    steps: list[Step],
) -> None:
    await s.execute(
        text(
            "UPDATE agent_runs SET status = :st, skip_reason = :sr, error_code = :ec, "
            "intents = :intents, knowledge_hash = :kh, model_calls = :mc, tool_calls = :tc, "
            "input_tokens = :i, cache_read_tokens = :cr, cache_write_tokens = :cw, "
            "output_tokens = :o, cost_micro = :cost, finished_at = now() "
            "WHERE tenant_id = :t AND id = :id"
        ),
        {
            "st": status,
            "sr": skip_reason,
            "ec": error_code,
            "intents": intents,
            "kh": knowledge_hash,
            "mc": totals.model_calls,
            "tc": totals.tool_calls,
            "i": totals.input_tokens,
            "cr": totals.cache_read_tokens,
            "cw": totals.cache_write_tokens,
            "o": totals.output_tokens,
            "cost": totals.cost_micro,
            "t": tenant_id,
            "id": run_id,
        },
    )
    for index, st in enumerate(steps):
        await s.execute(
            text(
                "INSERT INTO agent_run_steps (tenant_id, run_id, step_index, kind, name, status, "
                "latency_ms, detail) VALUES (:t, :r, :i, :k, :n, :st, :ms, CAST(:d AS jsonb))"
            ),
            {
                "t": tenant_id,
                "r": run_id,
                "i": index,
                "k": st.kind,
                "n": st.name,
                "st": st.status,
                "ms": st.latency_ms,
                "d": json.dumps(st.detail, ensure_ascii=False),
            },
        )


async def save_draft(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    run_id: UUID,
    conversation_id: UUID,
    based_on_sequence: int,
    draft: DraftToSave,
) -> UUID:
    draft_id = uuid4()
    await s.execute(
        text(
            "INSERT INTO agent_drafts (id, tenant_id, run_id, conversation_id, based_on_sequence, "
            "text, sources, grounded, needs_human, reason) "
            "VALUES (:id, :t, :r, :c, :seq, :text, CAST(:src AS jsonb), :g, :nh, :reason)"
        ),
        {
            "id": draft_id,
            "t": tenant_id,
            "r": run_id,
            "c": conversation_id,
            "seq": based_on_sequence,
            "text": draft.text,
            "src": json.dumps(draft.sources),
            "g": draft.grounded,
            "nh": draft.needs_human,
            "reason": draft.reason or None,
        },
    )
    return draft_id


async def emit_completed(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    run_id: UUID,
    conversation_id: UUID,
    draft_id: UUID | None,
    sequence: int,
    status: str,
) -> None:
    """Outbox `agent_run.completed`, cùng transaction với lưu nháp (staff UI nhận tín hiệu)."""
    await OutboxWriter(s).add(
        TenantContext(tenant_id),
        aggregate_type="agent_run",
        aggregate_id=run_id,
        event_type="agent_run.completed",
        payload={
            "tenant_id": str(tenant_id),
            "conversation_id": str(conversation_id),
            "run_id": str(run_id),
            "draft_id": str(draft_id) if draft_id else None,
            "sequence": sequence,
            "status": status,
        },
        trace_id=None,
    )
