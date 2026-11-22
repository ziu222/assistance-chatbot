from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.domain.entities import Conversation, Message
from app.modules.conversation.infrastructure.persistence.models import (
    ConversationModel,
    MessageModel,
)
from app.shared.domain.tenant import TenantContext


def _conversation(m: ConversationModel) -> Conversation:
    return Conversation(
        m.id,
        m.tenant_id,
        m.channel_connection_id,
        m.customer_id,
        m.status,
        m.last_sequence,
        m.last_message_at,
        m.last_message_preview,
        m.unread_count,
        m.version,
        m.created_at,
        m.updated_at,
        m.closed_at,
    )


def _message(m: MessageModel) -> Message:
    return Message(
        m.id,
        m.tenant_id,
        m.channel_connection_id,
        m.conversation_id,
        m.external_message_id,
        m.direction,
        m.sender_type,
        m.sender_ref,
        m.content,
        m.status,
        m.sequence,
        m.occurred_at,
        m.trace_id,
    )


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, ctx: TenantContext, customer_id: UUID, connection_id: UUID) -> Conversation:
        # Vi phạm uq_conversation_one_open ném IntegrityError; caller đọc lại bản đang mở.
        m = ConversationModel(
            id=uuid4(),
            tenant_id=ctx.tenant_id,
            customer_id=customer_id,
            channel_connection_id=connection_id,
        )
        self._s.add(m)
        await self._s.flush()
        await self._s.refresh(m)
        return _conversation(m)

    async def add_if_no_open(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID
    ) -> Conversation | None:
        """None nếu đã có conversation đang mở (uq_conversation_one_open). Không làm hỏng tx."""
        stmt = (
            pg_insert(ConversationModel)
            .values(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                customer_id=customer_id,
                channel_connection_id=connection_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationModel.tenant_id,
                    ConversationModel.customer_id,
                    ConversationModel.channel_connection_id,
                ],
                # Literal, không bind param: với prepared statement, sau vài lần Postgres dùng
                # generic plan và không suy ra được partial index từ "status = $n".
                index_where=text("status = 'open'"),
            )
            .returning(ConversationModel)
        )
        m = (await self._s.execute(stmt)).scalar_one_or_none()
        return _conversation(m) if m else None

    async def record_message(
        self,
        ctx: TenantContext,
        conversation_id: UUID,
        *,
        sequence: int,
        at: datetime,
        preview: str,
        inbound: bool,
    ) -> None:
        await self._s.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == ctx.tenant_id,
                ConversationModel.id == conversation_id,
            )
            .values(
                last_sequence=sequence,
                last_message_at=at,
                last_message_preview=preview,
                unread_count=ConversationModel.unread_count + (1 if inbound else 0),
                # Tin outbound duy nhất ghi qua đây là tin của nhân viên ⇒ hội thoại thuộc người,
                # cùng transaction với tin (bước 2 §12.3). Bot chỉ trả lại khi nhân viên bấm nút.
                handling=ConversationModel.handling if inbound else "human",
                version=ConversationModel.version + 1,
                updated_at=func.now(),
            )
        )

    async def get(
        self, ctx: TenantContext, conversation_id: UUID, *, lock: bool = False
    ) -> Conversation | None:
        q = select(ConversationModel).where(
            ConversationModel.tenant_id == ctx.tenant_id, ConversationModel.id == conversation_id
        )
        if lock:
            q = q.with_for_update()
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _conversation(m) if m else None

    async def mark_read(self, ctx: TenantContext, conversation_id: UUID) -> None:
        await self._s.execute(
            update(ConversationModel)
            .where(
                ConversationModel.tenant_id == ctx.tenant_id,
                ConversationModel.id == conversation_id,
            )
            .values(unread_count=0, version=ConversationModel.version + 1, updated_at=func.now())
        )

    async def find_open(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID, *, lock: bool = False
    ) -> Conversation | None:
        q = select(ConversationModel).where(
            ConversationModel.tenant_id == ctx.tenant_id,
            ConversationModel.customer_id == customer_id,
            ConversationModel.channel_connection_id == connection_id,
            ConversationModel.status == "open",
        )
        if lock:
            q = q.with_for_update()
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _conversation(m) if m else None

    async def latest_for_customer(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID
    ) -> Conversation | None:
        """Conversation hiện tại của khách: bản mới nhất (đang mở hoặc vừa đóng)."""
        q = (
            select(ConversationModel)
            .where(
                ConversationModel.tenant_id == ctx.tenant_id,
                ConversationModel.customer_id == customer_id,
                ConversationModel.channel_connection_id == connection_id,
            )
            .order_by(ConversationModel.created_at.desc(), ConversationModel.id.desc())
            .limit(1)
        )
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _conversation(m) if m else None


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self,
        ctx: TenantContext,
        *,
        conversation_id: UUID,
        connection_id: UUID,
        external_message_id: str,
        direction: str,
        sender_type: str,
        sender_ref: str | None,
        content: str,
        status: str,
        sequence: int,
        occurred_at: datetime,
        trace_id: str | None,
    ) -> Message | None:
        """Trả None nếu khóa idempotency đã tồn tại (tin trùng). Lỗi ràng buộc khác vẫn ném."""
        stmt = (
            pg_insert(MessageModel)
            .values(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                channel_connection_id=connection_id,
                conversation_id=conversation_id,
                external_message_id=external_message_id,
                direction=direction,
                sender_type=sender_type,
                sender_ref=sender_ref,
                content=content,
                status=status,
                sequence=sequence,
                occurred_at=occurred_at,
                trace_id=trace_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    MessageModel.tenant_id,
                    MessageModel.channel_connection_id,
                    MessageModel.direction,
                    MessageModel.external_message_id,
                ]
            )
            .returning(MessageModel)
        )
        m = (await self._s.execute(stmt)).scalar_one_or_none()
        return _message(m) if m else None

    async def get_by_external_id(
        self, ctx: TenantContext, connection_id: UUID, direction: str, external_message_id: str
    ) -> Message | None:
        q = select(MessageModel).where(
            MessageModel.tenant_id == ctx.tenant_id,
            MessageModel.channel_connection_id == connection_id,
            MessageModel.direction == direction,
            MessageModel.external_message_id == external_message_id,
        )
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _message(m) if m else None

    async def list_after(
        self, ctx: TenantContext, conversation_id: UUID, after_sequence: int = 0, limit: int = 100
    ) -> list[Message]:
        q = (
            select(MessageModel)
            .where(
                MessageModel.tenant_id == ctx.tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.sequence > after_sequence,
            )
            .order_by(MessageModel.sequence)
            .limit(limit)
        )
        return [_message(m) for m in (await self._s.execute(q)).scalars()]
