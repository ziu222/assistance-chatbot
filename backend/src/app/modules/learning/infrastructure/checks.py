"""Hậu kiểm ứng viên tri thức sau khi LLM sinh xong (thiết kế 05 §6.2 bước 4).

Tách riêng khỏi `candidates.py` (bộ sinh ứng viên) để bảo đảm bất biến E17: bộ sinh ứng viên không
bao giờ đọc tin `customer`; file này chỉ đọc tin `customer` để chạy kiểm tra chống trùng chuỗi > 8
từ bằng code và không bao giờ gọi LLM.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.learning.domain.candidates import CandidateChecks, check_candidate


async def verify_candidate(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_ids: Sequence[UUID],
    *,
    proposal_text: str,
    staff_texts: Sequence[str],
) -> CandidateChecks:
    customer_texts: list[str] = []
    if conversation_ids:
        rows = await s.execute(
            text(
                "SELECT content FROM messages "
                "WHERE tenant_id = :t AND conversation_id = ANY(:cs) "
                "  AND sender_type = 'customer' AND content IS NOT NULL"
            ),
            {"t": tenant_id, "cs": list(set(conversation_ids))},
        )
        customer_texts = [r[0] for r in rows]
    return check_candidate(proposal_text, staff_texts=staff_texts, customer_texts=customer_texts)
