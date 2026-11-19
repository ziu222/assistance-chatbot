import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Identity, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.db import Base


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    aggregate_type: Mapped[str]
    aggregate_id: Mapped[uuid.UUID]
    event_type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None]


class OutboxWriter:
    """Ghi event trong CÙNG transaction với thay đổi nghiệp vụ (transactional outbox)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self,
        ctx: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> None:
        self._s.add(
            OutboxEventModel(
                tenant_id=ctx.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload,
                trace_id=trace_id,
            )
        )
        await self._s.flush()
