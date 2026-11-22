"""Dữ liệu cho báo cáo tín hiệu leo thang: điểm cao nhất mỗi hội thoại + nhãn từ ticket."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def scored_conversations(
    s: AsyncSession, tenant_id: UUID, days: int
) -> list[tuple[int, bool]]:
    rows = await s.execute(
        text(
            "WITH per_run AS ("
            "  SELECT r.conversation_id, SUM((st.detail->>'score')::int) AS score "
            "  FROM agent_runs r JOIN agent_run_steps st "
            "    ON st.tenant_id = r.tenant_id AND st.run_id = r.id AND st.kind = 'signal' "
            "  WHERE r.tenant_id = :t AND r.started_at >= now() - make_interval(days => :d) "
            "  GROUP BY r.id, r.conversation_id"
            ") "
            "SELECT max(p.score), EXISTS ("
            "  SELECT 1 FROM handoff_requests h WHERE h.tenant_id = :t "
            "  AND h.conversation_id = p.conversation_id AND h.not_needed IS NOT TRUE"
            ") FROM per_run p GROUP BY p.conversation_id"
        ),
        {"t": tenant_id, "d": days},
    )
    return [(int(r[0]), bool(r[1])) for r in rows]
