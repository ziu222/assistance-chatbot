"""Hàng đợi run của agent trên Postgres (ADR-0008).

Một hàng mỗi hội thoại: dispatcher (relay) upsert khi có tin khách, cùng transaction đánh dấu
outbox ⇒ bền, at-least-once, idempotent. Cột `due_at` vừa là debounce (gom tin liên tiếp) vừa
cùng PK bảo đảm tối đa một run đang chạy mỗi hội thoại. Worker claim bằng SKIP LOCKED + lease;
`lease_token` mới mỗi lần claim là fencing token: worker có lease đã hết hạn không ghi đè được.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.infrastructure.outbox import OutboxEventModel


@dataclass(frozen=True)
class DebounceWindow:
    window: timedelta  # chờ thêm sau mỗi tin
    max_wait: timedelta  # trần tính từ tin đầu tiên của cụm


@dataclass(frozen=True)
class RunRequest:
    conversation_id: UUID
    tenant_id: UUID
    from_sequence: int
    to_sequence: int
    first_seen_at: datetime
    due_at: datetime
    lease_token: UUID
    attempts: int


_ENQUEUE = text(
    """
    INSERT INTO conversation_run_requests AS r
      (conversation_id, tenant_id, from_sequence, to_sequence, first_seen_at, due_at)
    VALUES (:conversation_id, :tenant_id, :seq, :seq, now(), now() + :window)
    ON CONFLICT (conversation_id) DO UPDATE SET
      to_sequence = GREATEST(r.to_sequence, EXCLUDED.to_sequence),
      due_at = LEAST(now() + :window, r.first_seen_at + :max_wait)
    """
)


async def enqueue_from_outbox(
    session: AsyncSession, event: OutboxEventModel, *, window: DebounceWindow
) -> bool:
    """Hook của relay: tin khách mới ⇒ đặt/gia hạn yêu cầu run. Trả True nếu đã enqueue.

    Idempotent: phát lại cùng event không tạo hàng mới, không lùi `to_sequence`.
    """
    p = event.payload
    if event.event_type != "message.created" or p.get("direction") != "inbound":
        return False
    await session.execute(
        _ENQUEUE,
        {
            "conversation_id": UUID(p["conversation_id"]),
            "tenant_id": event.tenant_id,
            "seq": int(p["sequence"]),
            "window": window.window,
            "max_wait": window.max_wait,
        },
    )
    return True


_CLAIM = text(
    """
    UPDATE conversation_run_requests r
    SET lease_until = now() + :lease, lease_token = :token, attempts = r.attempts + 1
    WHERE r.conversation_id = (
      SELECT conversation_id FROM conversation_run_requests
      WHERE due_at <= now() AND (lease_until IS NULL OR lease_until < now())
      ORDER BY due_at
      LIMIT 1
      FOR UPDATE SKIP LOCKED
    )
    RETURNING conversation_id, tenant_id, from_sequence, to_sequence, first_seen_at, due_at,
              lease_token, attempts
    """
)


async def claim_next(session: AsyncSession, lease: timedelta) -> RunRequest | None:
    """Nhận yêu cầu đã tới hạn và chưa có ai giữ. Gọi trong transaction ngắn rồi commit ngay."""
    row = (await session.execute(_CLAIM, {"lease": lease, "token": uuid4()})).first()
    return RunRequest(*row) if row else None


_DELETE_IF_UNCHANGED = text(
    "DELETE FROM conversation_run_requests "
    "WHERE conversation_id = :c AND lease_token = :token AND to_sequence = :to"
)
_RESCHEDULE_NEWER = text(
    """
    UPDATE conversation_run_requests
    SET from_sequence = :to + 1, first_seen_at = now(), due_at = now() + :window,
        lease_until = NULL, lease_token = NULL, attempts = 0
    WHERE conversation_id = :c AND lease_token = :token
    """
)


async def complete(
    session: AsyncSession, req: RunRequest, window: DebounceWindow
) -> Literal["done", "rescheduled", "lost"]:
    """Kết thúc run thành công.

    - done: không có tin mới ⇒ xóa yêu cầu.
    - rescheduled: có tin mới trong lúc chạy ⇒ giữ phần chưa xử lý, mở cửa sổ gom mới.
    - lost: lease đã bị worker khác lấy (hết hạn) ⇒ không ghi gì.
    """
    params = {"c": req.conversation_id, "token": req.lease_token, "to": req.to_sequence}
    if (await session.execute(_DELETE_IF_UNCHANGED, params)).rowcount:
        return "done"
    result = await session.execute(_RESCHEDULE_NEWER, params | {"window": window.window})
    return "rescheduled" if result.rowcount else "lost"


_RETRY = text(
    """
    UPDATE conversation_run_requests
    SET lease_until = NULL, lease_token = NULL, due_at = now() + :delay
    WHERE conversation_id = :c AND lease_token = :token
    """
)
_DROP = text(
    "DELETE FROM conversation_run_requests WHERE conversation_id = :c AND lease_token = :token"
)


async def retry_later(
    session: AsyncSession, req: RunRequest, delay: timedelta, *, max_attempts: int
) -> Literal["rescheduled", "dropped", "lost"]:
    """Lỗi tạm thời: hẹn chạy lại sau `delay`; quá `max_attempts` thì bỏ (không kẹt hàng đợi)."""
    params = {"c": req.conversation_id, "token": req.lease_token}
    if req.attempts >= max_attempts:
        return "dropped" if (await session.execute(_DROP, params)).rowcount else "lost"
    result = await session.execute(_RETRY, params | {"delay": delay})
    return "rescheduled" if result.rowcount else "lost"
