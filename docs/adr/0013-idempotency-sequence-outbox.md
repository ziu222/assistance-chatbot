# ADR-0013: Idempotency, sequence và transactional outbox

- Status: accepted
- Date: 2026-11-20

## Context

Tin inbound có thể bị gửi lặp (client retry, mạng chập chờn) và đến đồng thời. Mỗi conversation cần thứ tự hiển thị xác định, và mọi thay đổi phải phát sự kiện cho realtime (bước này) và agent (bước 2) mà không mất sự kiện khi process chết giữa chừng.

## Decision

- **Idempotency bằng unique index** `uq_message_idempotency (tenant_id, channel_connection_id, direction, external_message_id)` + `INSERT … ON CONFLICT DO NOTHING RETURNING`. Không trả dòng ⇒ đọc bản đã có, trả `200` cùng body.
- **Sequence liên tục theo conversation** cấp từ `conversations.last_sequence` dưới row lock (`SELECT … FOR UPDATE`). Lock này tuần tự hóa cả tin khách lẫn tin nhân viên.
- **Một conversation mở** mỗi (tenant, customer, connection) bằng partial unique index `uq_conversation_one_open`; bên thua race đọc lại bản đang mở.
- **Transactional outbox**: ghi `outbox_events` trong cùng transaction với message. Relay đọc `FOR UPDATE SKIP LOCKED`, publish, đánh dấu `published_at`, commit ⇒ **at-least-once**. Payload chỉ chứa ID, không chứa nội dung tin.
- Xung đột tạm thời (40001/40P01) ⇒ retry tối đa 3 lần (`retry_conflicts`), vượt ⇒ `503` + `Retry-After`; client retry cùng `client_message_id` nên an toàn.

## Alternatives considered

- Lock ứng dụng / Redis lock cho idempotency: sai khi Redis mất, thêm điểm lỗi.
- Sequence bằng `max(sequence)+1` không lock: trùng dưới đồng thời.
- Publish trực tiếp sau commit: mất sự kiện khi process chết giữa commit và publish.
- `LISTEN/NOTIFY` thay cho poll: tối ưu độ trễ, chưa cần ở bước 1 (poll 200 ms).

## Consequences

- Bất biến nằm trong DB, kiểm chứng bằng test đồng thời trên Postgres thật (50 request cùng id ⇒ 1 tin; 100 tin song song ⇒ sequence 1..100; 20 tin khách + 20 tin nhân viên ⇒ 1..41).
- Tín hiệu có thể trùng; client idempotent theo `(conversation_id, sequence)`.
- Bài học khi làm: predicate của `ON CONFLICT … WHERE` cho partial index **phải là literal SQL**. Bind param (`status = $n`) chạy được vài lần rồi hỏng khi Postgres chuyển prepared statement sang generic plan (có test hồi quy).

## Conditions to revisit

- Cần nhiều relay vì thông lượng: SKIP LOCKED đã an toàn, nhưng mất thứ tự toàn cục (thứ tự hiển thị vẫn đúng nhờ `sequence`).
- Độ trễ outbox → publish đo được là vấn đề: thêm `pg_notify` đánh thức relay, giữ poll làm lưới an toàn.
- Consumer cần độ bền (agent ở bước 2): không dùng Redis pub/sub; quyết định trong ADR-0008.
