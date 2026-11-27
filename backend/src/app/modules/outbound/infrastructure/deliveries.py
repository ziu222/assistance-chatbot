"""Hàng đợi giao tin kênh ngoài bền vững (`outbound_deliveries`) và worker bất đồng bộ (O5.3).

Mọi tin outbound đi kênh ngoài được ghi vào `outbound_deliveries` trong CÙNG DB transaction với
`messages`. `DeliveryWorker` claim theo lô ngắn (`FOR UPDATE SKIP LOCKED`), giải phóng transaction
trước khi gọi HTTP, rồi cập nhật kết quả ở transaction ngắn thứ hai.
"""

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.integration.infrastructure.messenger.connections import (
    MessengerConnection,
    by_connection_id,
)
from app.modules.outbound.domain.delivery import (
    MAX_DELIVERY_ATTEMPTS,
    ChannelSender,
    DeliveryOutcome,
    next_retry_delay,
)
from app.shared.infrastructure.crypto import (
    CredentialsKeyNotConfigured,
    InvalidEncryptedPayload,
)

log = logging.getLogger(__name__)

_CLAIM_LEASE = timedelta(seconds=300)

_CLAIM_DUE = text(
    """
    UPDATE outbound_deliveries d
    SET attempts = d.attempts + 1,
        next_attempt_at = now() + :lease
    WHERE d.id IN (
      SELECT id FROM outbound_deliveries
      WHERE status = 'pending' AND next_attempt_at <= now()
      ORDER BY next_attempt_at, id
      LIMIT :batch
      FOR UPDATE SKIP LOCKED
    )
    RETURNING d.id, d.tenant_id, d.message_id, d.channel_connection_id,
              d.provider, d.recipient_external_id, d.content, d.attempts
    """
)
_MARK_DELIVERED = text(
    "UPDATE outbound_deliveries "
    "SET status = 'delivered', provider_message_id = :pmid, "
    "    last_error = NULL, delivered_at = now() "
    "WHERE tenant_id = :t AND id = :id AND status = 'pending' AND attempts = :attempts"
)
_MARK_FAILED = text(
    "UPDATE outbound_deliveries "
    "SET status = 'failed', last_error = :err "
    "WHERE tenant_id = :t AND id = :id AND status = 'pending' AND attempts = :attempts"
)
_MARK_RETRY = text(
    "UPDATE outbound_deliveries "
    "SET next_attempt_at = now() + :delay, last_error = :err "
    "WHERE tenant_id = :t AND id = :id AND status = 'pending' AND attempts = :attempts"
)


@dataclass(frozen=True)
class _ClaimedDelivery:
    id: UUID
    tenant_id: UUID
    message_id: UUID
    channel_connection_id: UUID
    provider: str
    recipient_external_id: str
    content: str
    attempts: int


async def enqueue_delivery(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    message_id: UUID,
    connection_id: UUID,
    provider: str,
    recipient_external_id: str,
    content: str,
) -> None:
    """Đưa tin outbound vào hàng đợi kênh ngoài; bỏ qua `webchat`, idempotent theo message."""
    if provider == "webchat" or not recipient_external_id:
        return
    await s.execute(
        text(
            "INSERT INTO outbound_deliveries "
            "(id, tenant_id, message_id, channel_connection_id, provider, "
            " recipient_external_id, content) "
            "VALUES (:id, :t, :mid, :conn, :provider, :recip, :content) "
            "ON CONFLICT ON CONSTRAINT uq_outbound_delivery_message DO NOTHING"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "mid": message_id,
            "conn": connection_id,
            "provider": provider,
            "recip": recipient_external_id,
            "content": content,
        },
    )


class DeliveryWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        credentials_key: str,
        senders: Mapping[str, ChannelSender],
        *,
        batch_size: int = 20,
        idle_sleep: float = 0.5,
    ) -> None:
        self._factory = session_factory
        self._credentials_key = credentials_key
        self._senders = senders
        self._batch_size = batch_size
        self._idle_sleep = idle_sleep

    async def _persist_outcome(self, item: _ClaimedDelivery, outcome: DeliveryOutcome) -> None:
        async with self._factory() as s, s.begin():
            if outcome.ok:
                res = await s.execute(
                    _MARK_DELIVERED,
                    {
                        "t": item.tenant_id,
                        "id": item.id,
                        "attempts": item.attempts,
                        "pmid": outcome.provider_message_id,
                    },
                )
            elif not outcome.retryable or item.attempts >= MAX_DELIVERY_ATTEMPTS:
                res = await s.execute(
                    _MARK_FAILED,
                    {
                        "t": item.tenant_id,
                        "id": item.id,
                        "attempts": item.attempts,
                        "err": (outcome.error or "delivery_failed")[:500],
                    },
                )
                if res.rowcount:
                    log.warning(
                        "outbound delivery failed",
                        extra={
                            "delivery_id": str(item.id),
                            "provider": item.provider,
                            "attempts": item.attempts,
                        },
                    )
            else:
                res = await s.execute(
                    _MARK_RETRY,
                    {
                        "t": item.tenant_id,
                        "id": item.id,
                        "attempts": item.attempts,
                        "delay": next_retry_delay(item.attempts),
                        "err": (outcome.error or "retryable_error")[:500],
                    },
                )
            if res.rowcount == 0:
                log.warning(
                    "outbound delivery fencing lost",
                    extra={
                        "delivery_id": str(item.id),
                        "provider": item.provider,
                        "attempts": item.attempts,
                    },
                )

    async def run_once(self) -> int:
        """Nhận tối đa `batch_size` bản ghi tới hạn, gọi HTTP ngoài transaction và lưu từng tin."""
        claimed: list[tuple[_ClaimedDelivery, MessengerConnection | None, str | None]] = []
        async with self._factory() as s, s.begin():
            rows = (
                await s.execute(
                    _CLAIM_DUE,
                    {"batch": self._batch_size, "lease": _CLAIM_LEASE},
                )
            ).all()
            for r in rows:
                item = _ClaimedDelivery(*r)
                conn: MessengerConnection | None = None
                load_error: str | None = None
                if item.provider == "messenger":
                    try:
                        conn = await by_connection_id(
                            s, item.tenant_id, item.channel_connection_id, self._credentials_key
                        )
                    except (CredentialsKeyNotConfigured, InvalidEncryptedPayload) as exc:
                        load_error = type(exc).__name__
                    else:
                        if conn is None or not conn.page_access_token:
                            load_error = "connection_unavailable"
                claimed.append((item, conn, load_error))

        if not claimed:
            return 0

        for item, conn, load_error in claimed:
            sender = self._senders.get(item.provider)
            if sender is None:
                outcome = DeliveryOutcome(
                    ok=False,
                    retryable=False,
                    error=f"unsupported_provider:{item.provider}",
                )
            elif load_error is not None or conn is None:
                outcome = DeliveryOutcome(
                    ok=False,
                    retryable=False,
                    error=load_error or "connection_unavailable",
                )
            else:
                try:
                    outcome = await sender.send(
                        recipient_id=item.recipient_external_id,
                        text=item.content,
                        access_token=conn.page_access_token,
                    )
                except Exception as exc:
                    outcome = DeliveryOutcome(ok=False, retryable=True, error=type(exc).__name__)
            await self._persist_outcome(item, outcome)

        return len(claimed)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = 0
            try:
                processed = await self.run_once()
            except Exception:
                log.exception("delivery worker tick failed")
            if processed:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep)
