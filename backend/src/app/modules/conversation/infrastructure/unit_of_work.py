from typing import Self

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.conversation.infrastructure.persistence.repository import (
    ConversationRepository,
    MessageRepository,
)
from app.modules.customer.infrastructure.persistence.repository import CustomerRepository
from app.shared.application.errors import RetryableConflict
from app.shared.infrastructure.outbox import OutboxWriter

# serialization_failure, deadlock_detected
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


class SqlConversationUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self.customers = CustomerRepository(self._session)
        self.conversations = ConversationRepository(self._session)
        self.messages = MessageRepository(self._session)
        self.outbox = OutboxWriter(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # close() rollback mọi transaction chưa commit.
        await self._session.close()
        if isinstance(exc, DBAPIError) and _sqlstate(exc) in RETRYABLE_SQLSTATES:
            raise RetryableConflict(str(exc.orig)) from exc

    async def commit(self) -> None:
        await self._session.commit()
