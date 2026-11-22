"""Truy cập dữ liệu chính sách: SQL thẳng, mọi câu lọc tenant_id."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation_policy.domain.policy import Decision, PolicyConfig, PolicyState


@dataclass(frozen=True)
class Release:
    id: UUID
    number: int
    config: PolicyConfig
    created_at: datetime
    current: bool


async def current_release(s: AsyncSession, tenant_id: UUID) -> Release | None:
    row = (
        await s.execute(
            text(
                "SELECT r.id, r.number, r.config, r.created_at FROM tenant_policy p "
                "JOIN policy_releases r ON r.tenant_id = p.tenant_id "
                "  AND r.id = p.current_release_id "
                "WHERE p.tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if row is None:
        return None
    return Release(row[0], row[1], PolicyConfig.model_validate(row[2]), row[3], True)


async def list_releases(s: AsyncSession, tenant_id: UUID, limit: int = 20) -> list[Release]:
    rows = await s.execute(
        text(
            "SELECT r.id, r.number, r.config, r.created_at, "
            "COALESCE(p.current_release_id = r.id, false) FROM policy_releases r "
            "LEFT JOIN tenant_policy p ON p.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = :t ORDER BY r.number DESC LIMIT :n"
        ),
        {"t": tenant_id, "n": limit},
    )
    return [Release(r[0], r[1], PolicyConfig.model_validate(r[2]), r[3], r[4]) for r in rows]


async def _point_to(s: AsyncSession, tenant_id: UUID, release_id: UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO tenant_policy (tenant_id, current_release_id) VALUES (:t, :r) "
            "ON CONFLICT (tenant_id) DO UPDATE SET current_release_id = :r, updated_at = now()"
        ),
        {"t": tenant_id, "r": release_id},
    )


async def publish(
    s: AsyncSession, tenant_id: UUID, config: PolicyConfig, created_by: UUID
) -> Release:
    """Lưu = tạo release mới (bất biến) + trỏ tới nó. Số thứ tự tăng dần theo tenant; hai admin
    lưu cùng lúc thì một bên vi phạm UNIQUE và nhận lỗi (route trả 409)."""
    rid = uuid4()
    row = (
        await s.execute(
            text(
                "INSERT INTO policy_releases (id, tenant_id, number, config, created_by) "
                "SELECT :id, :t, COALESCE(MAX(number), 0) + 1, CAST(:cfg AS jsonb), :by "
                "FROM policy_releases WHERE tenant_id = :t RETURNING number, created_at"
            ),
            {
                "id": rid,
                "t": tenant_id,
                "cfg": config.model_dump_json(),
                "by": created_by,
            },
        )
    ).one()
    await _point_to(s, tenant_id, rid)
    return Release(rid, row[0], config, row[1], True)


async def restore(s: AsyncSession, tenant_id: UUID, release_id: UUID) -> bool:
    exists = (
        await s.execute(
            text("SELECT 1 FROM policy_releases WHERE tenant_id = :t AND id = :r"),
            {"t": tenant_id, "r": release_id},
        )
    ).first()
    if exists is None:
        return False
    await _point_to(s, tenant_id, release_id)
    return True


@dataclass(frozen=True)
class ConversationForPolicy:
    handling: str
    status: str
    shop_name: str


async def lock_conversation(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> ConversationForPolicy | None:
    """FOR UPDATE: tuần tự hóa với tin của nhân viên (cũng khóa hội thoại) nên đổi `handling` không
    ghi đè lên việc nhân viên vừa nhận hội thoại."""
    row = (
        await s.execute(
            text(
                "SELECT c.handling, c.status, t.name FROM conversations c "
                "JOIN tenants t ON t.id = c.tenant_id "
                "WHERE c.tenant_id = :t AND c.id = :c FOR UPDATE OF c"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).first()
    return ConversationForPolicy(*row) if row else None


async def customer_texts(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, from_sequence: int, to_sequence: int
) -> list[str]:
    rows = await s.execute(
        text(
            "SELECT content FROM messages WHERE tenant_id = :t AND conversation_id = :c "
            "AND sequence BETWEEN :f AND :to AND sender_type = 'customer' ORDER BY sequence"
        ),
        {"t": tenant_id, "c": conversation_id, "f": from_sequence, "to": to_sequence},
    )
    return [r[0] for r in rows]


async def decision_exists(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, to_sequence: int
) -> bool:
    row = await s.execute(
        text(
            "SELECT 1 FROM policy_decisions "
            "WHERE tenant_id = :t AND conversation_id = :c AND to_sequence = :q"
        ),
        {"t": tenant_id, "c": conversation_id, "q": to_sequence},
    )
    return row.first() is not None


async def load_state(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, handling: str, from_sequence: int
) -> PolicyState:
    base = {"t": tenant_id, "c": conversation_id, "f": from_sequence}
    prev = (
        await s.execute(
            text(
                "SELECT max(occurred_at) FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c AND sequence < :f"
            ),
            base,
        )
    ).scalar()
    last = await s.execute(
        text(
            "SELECT situation, max(created_at) FROM policy_decisions "
            "WHERE tenant_id = :t AND conversation_id = :c AND situation IS NOT NULL "
            "GROUP BY situation"
        ),
        base,
    )
    return PolicyState(
        handling=handling,  # type: ignore[arg-type]
        is_new_conversation=prev is None,
        prev_message_at=prev,
        last_sent={r[0]: r[1] for r in last},
    )


async def record(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    to_sequence: int,
    release_id: UUID,
    handling_before: str,
    decision: Decision,
) -> None:
    await s.execute(
        text(
            "INSERT INTO policy_decisions (id, tenant_id, conversation_id, to_sequence, "
            "policy_release_id, situation, canned_text, handling_before, set_handling, run_agent) "
            "VALUES (:id, :t, :c, :q, :r, :sit, :txt, :hb, :sh, :ra) "
            "ON CONFLICT (tenant_id, conversation_id, to_sequence) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "c": conversation_id,
            "q": to_sequence,
            "r": release_id,
            "sit": decision.situation,
            "txt": decision.text,
            "hb": handling_before,
            "sh": decision.set_handling,
            "ra": decision.run_agent,
        },
    )
    if decision.set_handling is not None:
        await set_handling(s, tenant_id, conversation_id, decision.set_handling)


async def set_handling(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    to: str,
    *,
    expected_version: int | None = None,
) -> bool:
    """Đổi trạng thái xử lý. `expected_version` ⇒ compare-and-set (nút "Trả lại cho bot")."""
    result = await s.execute(
        text(
            "UPDATE conversations SET handling = :h, version = version + 1, updated_at = now() "
            "WHERE tenant_id = :t AND id = :c "
            "AND (CAST(:v AS int) IS NULL OR version = CAST(:v AS int))"
        ),
        {"h": to, "t": tenant_id, "c": conversation_id, "v": expected_version},
    )
    return result.rowcount == 1


def config_json(config: PolicyConfig) -> dict:
    return json.loads(config.model_dump_json())
