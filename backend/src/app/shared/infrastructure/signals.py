"""Ghi `learning_signals` append-only dùng chung giữa các module (§12.9).

Payload đã chiếu: chỉ chứa id và nhãn, không chứa nội dung khách hay dữ liệu cá nhân.
"""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_signal(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID | None, kind: str, payload: dict
) -> None:
    """`learning_signals` append-only (§12.9). Payload đã chiếu: id/nhãn, không nội dung khách."""
    await s.execute(
        text(
            "INSERT INTO learning_signals (tenant_id, conversation_id, kind, payload) "
            "VALUES (:t, :c, :k, CAST(:p AS jsonb))"
        ),
        {"t": tenant_id, "c": conversation_id, "k": kind, "p": json.dumps(payload)},
    )
