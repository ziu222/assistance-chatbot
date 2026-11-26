"""Read model của inbox: SQL thẳng, mọi truy vấn lọc tenant_id từ session nhân viên."""

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.outbound.domain.channels import capabilities_for
from app.shared.domain.tenant import TenantContext

_SELECT = (
    "SELECT c.id, c.status, c.last_sequence, c.last_message_at, c.last_message_preview, "
    "c.unread_count, c.version, c.handling, c.created_at, c.closed_at, "
    "cu.id AS customer_id, cu.display_name AS customer_name, "
    "ch.id AS connection_id, ch.provider "
    "FROM conversations c "
    "JOIN customers cu ON (cu.tenant_id, cu.id) = (c.tenant_id, c.customer_id) "
    "JOIN channel_connections ch ON (ch.tenant_id, ch.id) = (c.tenant_id, c.channel_connection_id) "
    "WHERE c.tenant_id = :t "
)


class InvalidCursor(ValueError):
    pass


def encode_cursor(at: datetime, conversation_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{conversation_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        at, cid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(at), UUID(cid)
    except (ValueError, UnicodeError) as exc:
        raise InvalidCursor from exc


def _capabilities_json(provider: str) -> dict | None:
    caps = capabilities_for(provider)
    if caps is None:
        return None
    return {
        "reply_window_seconds": (
            int(caps.reply_window.total_seconds()) if caps.reply_window is not None else None
        ),
        "supports_links": caps.supports_links,
        "supports_buttons": caps.supports_buttons,
        "max_text_length": caps.max_text_length,
    }


def conversation_json(r) -> dict:
    return {
        "id": str(r.id),
        "status": r.status,
        "last_sequence": r.last_sequence,
        "last_message_at": r.last_message_at.isoformat() if r.last_message_at else None,
        "last_message_preview": r.last_message_preview,
        "unread_count": r.unread_count,
        "version": r.version,
        "handling": r.handling,
        "created_at": r.created_at.isoformat(),
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "customer": {"id": str(r.customer_id), "display_name": r.customer_name},
        "connection": {"id": str(r.connection_id), "provider": r.provider},
        "capabilities": _capabilities_json(r.provider),
    }


async def list_conversations(
    s: AsyncSession,
    ctx: TenantContext,
    *,
    status: str | None,
    cursor: str | None,
    limit: int,
    provider: str | None = None,
) -> tuple[list, str | None]:
    sql = _SELECT + "AND c.last_message_at IS NOT NULL "  # chỉ hội thoại đã có tin
    params: dict = {"t": ctx.tenant_id, "limit": limit + 1}
    if status:
        sql += "AND c.status = :status "
        params["status"] = status
    if provider:
        sql += "AND ch.provider = :provider "
        params["provider"] = provider
    if cursor:
        params["at"], params["cid"] = decode_cursor(cursor)
        sql += "AND (c.last_message_at, c.id) < (:at, :cid) "
    # Khớp index ix_conversation_list (tenant_id, status, last_message_at DESC, id DESC).
    sql += "ORDER BY c.last_message_at DESC, c.id DESC LIMIT :limit"
    rows = (await s.execute(text(sql), params)).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].last_message_at, page[-1].id) if len(rows) > limit else None
    )
    return page, next_cursor


async def get_conversation(s: AsyncSession, ctx: TenantContext, conversation_id: UUID):
    return (
        await s.execute(
            text(_SELECT + "AND c.id = :id"), {"t": ctx.tenant_id, "id": conversation_id}
        )
    ).first()


@dataclass(frozen=True)
class ChannelReplyState:
    provider: str
    config: dict[str, Any]
    last_customer_at: datetime | None
    outbound_since_customer: int


async def channel_reply_state(
    s: AsyncSession, ctx: TenantContext, conversation_id: UUID
) -> ChannelReplyState | None:
    """Lấy trạng thái cửa sổ/hạn mức kênh của hội thoại để kiểm trước khi nhân viên gửi."""
    row = (
        await s.execute(
            text(
                "SELECT ch.provider, ch.config, "
                "  (SELECT max(m.occurred_at) FROM messages m "
                "   WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.id "
                "     AND m.sender_type = 'customer') AS last_customer_at, "
                "  (SELECT count(*) FROM messages m "
                "   WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.id "
                "     AND m.direction = 'outbound' "
                "     AND m.sequence > COALESCE("
                "       (SELECT max(mc.sequence) FROM messages mc "
                "        WHERE mc.tenant_id = c.tenant_id AND mc.conversation_id = c.id "
                "          AND mc.sender_type = 'customer'), 0)) AS outbound_since "
                "FROM conversations c "
                "JOIN channel_connections ch "
                "  ON (ch.tenant_id, ch.id) = (c.tenant_id, c.channel_connection_id) "
                "WHERE c.tenant_id = :t AND c.id = :id"
            ),
            {"t": ctx.tenant_id, "id": conversation_id},
        )
    ).first()
    if row is None:
        return None
    return ChannelReplyState(
        provider=row.provider,
        config=row.config if isinstance(row.config, dict) else {},
        last_customer_at=row.last_customer_at,
        outbound_since_customer=int(row.outbound_since or 0),
    )
