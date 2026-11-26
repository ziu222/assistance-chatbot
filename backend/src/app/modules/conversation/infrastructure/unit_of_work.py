from typing import Self
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.conversation.infrastructure.persistence.repository import (
    ConversationRepository,
    MessageRepository,
)
from app.modules.customer.infrastructure.persistence.repository import CustomerRepository
from app.modules.outbound.infrastructure.deliveries import enqueue_delivery
from app.shared.application.errors import RetryableConflict
from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.outbox import OutboxWriter

# serialization_failure, deadlock_detected
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


class SqlDeliveryWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def enqueue(
        self,
        ctx: TenantContext,
        *,
        message_id: UUID,
        connection_id: UUID,
        provider: str,
        recipient_external_id: str,
        content: str,
    ) -> None:
        await enqueue_delivery(
            self._s,
            tenant_id=ctx.tenant_id,
            message_id=message_id,
            connection_id=connection_id,
            provider=provider,
            recipient_external_id=recipient_external_id,
            content=content,
        )


class SqlConversationUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self.customers = CustomerRepository(self._session)
        self.conversations = ConversationRepository(self._session)
        self.messages = MessageRepository(self._session)
        self.outbox = OutboxWriter(self._session)
        self.deliveries = SqlDeliveryWriter(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # close() rollback mọi transaction chưa commit.
        await self._session.close()
        if isinstance(exc, DBAPIError) and _sqlstate(exc) in RETRYABLE_SQLSTATES:
            raise RetryableConflict(str(exc.orig)) from exc

    async def commit(self) -> None:
        await self._session.commit()
