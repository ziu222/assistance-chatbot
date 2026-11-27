"""Ghi usage/chi phí mỗi lần gọi LLM (append-only) và đếm chi tiêu trong ngày để kiểm tra trần."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_runtime.llm.port import LLMResult
from app.modules.agent_runtime.llm.pricing import PRICE_VERSION, cost_micro
from app.shared.infrastructure.metrics import LLM_COST_MICRO, LLM_TOKENS

# Ngày tính theo UTC. ponytail: đổi sang múi giờ của tenant khi có yêu cầu thật.
_UTC_MIDNIGHT = "(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"


async def record_usage(
    s: AsyncSession, *, tenant_id: UUID, run_id: UUID | None, route: str, result: LLMResult
) -> int:
    """Ghi một dòng usage cho kết quả đã có. Trả chi phí micro-USD.

    Model chưa có giá ⇒ UnknownPrice và KHÔNG ghi gì: không bao giờ ghi chi phí 0 một cách ngầm.
    """
    target, usage = result.served_by, result.usage
    cost = cost_micro(target.provider, target.model, usage)
    failed = ",".join(f"{a.target.provider}:{a.reason}" for a in result.failover_from) or None
    await s.execute(
        text(
            "INSERT INTO usage_events (tenant_id, run_id, purpose, provider, model, input_tokens, "
            "cache_read_tokens, cache_write_tokens, output_tokens, cost_micro, price_version, "
            "failed_over_from) VALUES (:t, :run, :purpose, :provider, :model, :i, :cr, :cw, :o, "
            ":cost, :pv, :failed)"
        ),
        {
            "t": tenant_id,
            "run": run_id,
            "purpose": route,
            "provider": target.provider,
            "model": target.model,
            "i": usage.input,
            "cr": usage.cache_read,
            "cw": usage.cache_write,
            "o": usage.output,
            "cost": cost,
            "pv": PRICE_VERSION,
            "failed": failed,
        },
    )
    for kind, n in (
        ("input", usage.input),
        ("cache_read", usage.cache_read),
        ("cache_write", usage.cache_write),
        ("output", usage.output),
    ):
        LLM_TOKENS.labels(provider=target.provider, kind=kind).inc(n)
    LLM_COST_MICRO.labels(provider=target.provider).inc(cost)
    return cost


AUTHORING = "authoring"  # trợ lý viết có trần riêng, không ăn vào trần của nháp (§12.8)
JUDGE = "judge"  # giám khảo so sánh shadow (thiết kế 04 §6.2): trần riêng
LEARNING = "learning"  # dựng ứng viên + eval bài học (thiết kế 05 §6.2): trần riêng
HANDOFF_SUMMARY = "handoff_summary"  # tóm tắt ticket 3 dòng (bước 2 §12.5): trần riêng
OWN_CAPS = (AUTHORING, JUDGE, LEARNING, HANDOFF_SUMMARY)


async def spent_today_micro(s: AsyncSession, tenant_id: UUID, *, purpose: str | None = None) -> int:
    """Chi tiêu LLM của tenant từ 00:00 UTC hôm nay (trần chi phí ngày, thiết kế §7.3).

    `purpose=None`: đường trả lời khách (mọi mục đích trừ những mục có trần riêng); có giá trị:
    chỉ mục đích đó (trợ lý viết, giám khảo)."""
    if purpose is None:
        cond, params = "purpose <> ALL(:own)", {"own": list(OWN_CAPS)}
    else:
        cond, params = "purpose = :p", {"p": purpose}
    row = await s.execute(
        text(
            "SELECT coalesce(sum(cost_micro), 0) FROM usage_events "
            f"WHERE tenant_id = :t AND created_at >= {_UTC_MIDNIGHT} AND {cond}"
        ),
        {"t": tenant_id} | params,
    )
    return int(row.scalar_one())
