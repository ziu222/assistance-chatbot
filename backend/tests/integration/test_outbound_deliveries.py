"""Kiểm thử hàng đợi `outbound_deliveries` và `DeliveryWorker` cho Messenger (O5.3)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.outbound.ai_reply import send_ai_reply
from app.modules.outbound.canned import send_canned
from app.modules.outbound.domain.delivery import MAX_DELIVERY_ATTEMPTS, DeliveryOutcome
from app.modules.outbound.infrastructure.deliveries import DeliveryWorker
from app.shared.infrastructure.crypto import encrypt_json

from .factories import make_conversation, make_customer, make_tenant

TEST_CRED_KEY = Fernet.generate_key().decode("ascii")
PAGE_ACCESS_TOKEN = "EAAB-test-page-token"


class _FakeSender:
    def __init__(self, outcomes: list[DeliveryOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str, str]] = []

    async def send(self, *, recipient_id: str, text: str, access_token: str) -> DeliveryOutcome:
        self.calls.append((recipient_id, text, access_token))
        return self._outcomes.pop(0)


async def _setup_messenger_conv(session_factory, *, hours_ago: int = 1, handling: str = "bot"):
    encrypted = encrypt_json(
        {
            "app_secret": "sec",
            "verify_token": "tok",
            "page_access_token": PAGE_ACCESS_TOKEN,
        },
        TEST_CRED_KEY,
    )
    async with session_factory() as s, s.begin():
        ctx = await make_tenant(s)
        conn_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO channel_connections "
                "(id, tenant_id, provider, external_account_id, status, config, "
                " credentials_encrypted) "
                "VALUES (:id, :t, 'messenger', :ext, 'active', '{}'::jsonb, :cred)"
            ),
            {
                "id": conn_id,
                "t": ctx.tenant_id,
                "ext": f"page_{conn_id.hex[:8]}",
                "cred": encrypted,
            },
        )
        cust_id = await make_customer(s, ctx)
        await s.execute(
            text(
                "INSERT INTO channel_identities "
                "(id, tenant_id, customer_id, channel_connection_id, provider, scoped_external_id) "
                "VALUES (:id, :t, :cu, :conn, 'messenger', :psid)"
            ),
            {"id": uuid4(), "t": ctx.tenant_id, "cu": cust_id, "conn": conn_id, "psid": "psid_99"},
        )
        conv_id = await make_conversation(s, ctx, cust_id, conn_id, status="open")
        await s.execute(
            text(
                "INSERT INTO messages "
                "(id, tenant_id, channel_connection_id, conversation_id, external_message_id, "
                " direction, sender_type, content, status, sequence, occurred_at) "
                "VALUES (:id, :t, :conn, :c, :ext, 'inbound', 'customer', 'Shop ơi', 'received', "
                " 1, :at)"
            ),
            {
                "id": uuid4(),
                "t": ctx.tenant_id,
                "conn": conn_id,
                "c": conv_id,
                "ext": f"mid_{uuid4()}",
                "at": datetime.now(UTC) - timedelta(hours=hours_ago),
            },
        )
        await s.execute(
            text(
                "UPDATE conversations SET last_sequence = 1, handling = :h "
                "WHERE tenant_id = :t AND id = :c"
            ),
            {"h": handling, "t": ctx.tenant_id, "c": conv_id},
        )
    return ctx, conn_id, conv_id


async def test_send_ai_reply_enqueues_delivery_and_worker_delivers(session_factory):
    ctx, _conn_id, conv_id = await _setup_messenger_conv(session_factory, hours_ago=1)
    async with session_factory() as s, s.begin():
        res = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv_id,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Dạ shop mở cửa tới 21h ạ.",
        )
    assert res.sent is True and res.message_id is not None

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, provider, recipient_external_id, content, attempts "
                    "FROM outbound_deliveries WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )
        ).one()
    assert row.status == "pending"
    assert row.provider == "messenger"
    assert row.recipient_external_id == "psid_99"
    assert row.content == "Dạ shop mở cửa tới 21h ạ."
    assert row.attempts == 0

    sender = _FakeSender(
        [DeliveryOutcome(ok=True, retryable=False, provider_message_id="mid.$meta_1")]
    )
    worker = DeliveryWorker(session_factory, TEST_CRED_KEY, {"messenger": sender})
    processed = await worker.run_once()
    assert processed == 1
    assert sender.calls == [("psid_99", "Dạ shop mở cửa tới 21h ạ.", PAGE_ACCESS_TOKEN)]

    async with session_factory() as s:
        after = (
            await s.execute(
                text(
                    "SELECT status, provider_message_id, delivered_at, attempts "
                    "FROM outbound_deliveries WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )
        ).one()
    assert after.status == "delivered"
    assert after.provider_message_id == "mid.$meta_1"
    assert after.delivered_at is not None
    assert after.attempts == 1


async def test_delivery_worker_retries_500_then_fails_after_max_attempts(session_factory):
    ctx, _conn_id, conv_id = await _setup_messenger_conv(session_factory, hours_ago=1)
    async with session_factory() as s, s.begin():
        res = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv_id,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Thử retry",
        )
    assert res.sent is True

    sender = _FakeSender(
        [DeliveryOutcome(ok=False, retryable=True, error="http_500")] * MAX_DELIVERY_ATTEMPTS
    )
    worker = DeliveryWorker(session_factory, TEST_CRED_KEY, {"messenger": sender})

    for attempt in range(1, MAX_DELIVERY_ATTEMPTS):
        assert await worker.run_once() == 1
        async with session_factory() as s, s.begin():
            row = (
                await s.execute(
                    text(
                        "SELECT status, attempts, next_attempt_at > now() AS pushed "
                        "FROM outbound_deliveries WHERE tenant_id = :t AND message_id = :m"
                    ),
                    {"t": ctx.tenant_id, "m": res.message_id},
                )
            ).one()
            assert row.status == "pending"
            assert row.attempts == attempt
            assert row.pushed is True
            # Kéo `next_attempt_at` về quá khứ để chạy tiếp lần thử kế trong test.
            await s.execute(
                text(
                    "UPDATE outbound_deliveries SET next_attempt_at = now() - interval '1 second' "
                    "WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )

    # Quá 5 lần ⇒ chuyển sang `failed`
    assert await worker.run_once() == 1
    async with session_factory() as s:
        final = (
            await s.execute(
                text(
                    "SELECT status, attempts, last_error FROM outbound_deliveries "
                    "WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )
        ).one()
    assert final.status == "failed"
    assert final.attempts == MAX_DELIVERY_ATTEMPTS
    assert final.last_error == "http_500"


async def test_delivery_worker_marks_400_failed_immediately(session_factory):
    ctx, _conn_id, conv_id = await _setup_messenger_conv(session_factory, hours_ago=1)
    async with session_factory() as s, s.begin():
        res = await send_ai_reply(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv_id,
            draft_id=uuid4(),
            run_id=uuid4(),
            based_on_sequence=1,
            content="Lỗi 400",
        )
    assert res.sent is True

    sender = _FakeSender([DeliveryOutcome(ok=False, retryable=False, error="http_400: invalid")])
    worker = DeliveryWorker(session_factory, TEST_CRED_KEY, {"messenger": sender})
    assert await worker.run_once() == 1

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, attempts, last_error FROM outbound_deliveries "
                    "WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.attempts == 1
    assert row.last_error == "http_400: invalid"


async def test_send_staff_message_enqueues_delivery_for_messenger(session_factory):
    ctx, _conn_id, conv_id = await _setup_messenger_conv(session_factory, hours_ago=1)
    user_id = uuid4()
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name) "
                "VALUES (:id, :e, 'h', 'NV')"
            ),
            {"id": user_id, "e": f"nv-{user_id.hex[:6]}@shop.vn"},
        )
        await s.execute(
            text(
                "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (:t, :u, 'agent')"
            ),
            {"t": ctx.tenant_id, "u": user_id},
        )

    res = await SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))(
        ctx,
        conv_id,
        user_id=user_id,
        client_message_id=str(uuid4()),
        text="Em chào anh/chị ạ!",
        trace_id="trace-1",
    )
    assert res.created is True

    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT provider, recipient_external_id, content, status "
                    "FROM outbound_deliveries WHERE tenant_id = :t AND message_id = :m"
                ),
                {"t": ctx.tenant_id, "m": res.message_id},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].provider == "messenger"
    assert rows[0].recipient_external_id == "psid_99"
    assert rows[0].content == "Em chào anh/chị ạ!"
    assert rows[0].status == "pending"


async def test_send_canned_enqueues_delivery_for_messenger(session_factory):
    ctx, _conn_id, conv_id = await _setup_messenger_conv(session_factory, hours_ago=1)
    async with session_factory() as s, s.begin():
        sent = await send_canned(
            s,
            tenant_id=ctx.tenant_id,
            conversation_id=conv_id,
            to_sequence=1,
            situation="welcome",
            release_id=uuid4(),
            content="Cảm ơn bạn đã nhắn tin!",
        )
    assert sent is True

    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT provider, recipient_external_id, content, status "
                    "FROM outbound_deliveries WHERE tenant_id = :t"
                ),
                {"t": ctx.tenant_id},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].provider == "messenger"
    assert rows[0].recipient_external_id == "psid_99"
    assert rows[0].content == "Cảm ơn bạn đã nhắn tin!"
    assert rows[0].status == "pending"
