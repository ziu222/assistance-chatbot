# Runbook: outbox relay

Relay (`app.entrypoints.worker`) đọc `outbox_events` chưa publish và đẩy **tín hiệu** lên Redis pub/sub. Bước 1 chạy **đúng một** instance. Mất relay không mất tin (tin đã nằm trong DB); chỉ làm realtime chậm — client tự polling 30 s.

## Dấu hiệu

| Metric / dấu hiệu | Ý nghĩa | Ngưỡng cảnh báo gợi ý |
|---|---|---|
| `outbox_oldest_age_seconds` (API `/metrics`) | tuổi event cũ nhất chưa publish | > 30 s trong 5 phút |
| `outbox_unpublished_count` | số event tồn | tăng liên tục |
| `outbox_publish_lag_seconds` (worker `:9101/metrics`) | `published_at − created_at` | p95 > 2 s |
| Inbox hiện "Đang kết nối lại…" | SSE/Redis có vấn đề | — |
| `/health/ready` trả `"redis": "down"` | API không nói chuyện được với Redis | — |

`/metrics` là endpoint **nội bộ**: reverse proxy không được mở ra Internet.

## Chẩn đoán

1. Relay còn chạy không? Xem log JSON của worker (`logger: app.workers.outbox_relay`).
2. Có lỗi publish không?

   ```sql
   SELECT id, event_type, attempts, last_error, created_at
   FROM outbox_events
   WHERE published_at IS NULL
   ORDER BY id
   LIMIT 20;
   ```

   - `attempts` tăng + `last_error` dạng `ConnectionError: …` ⇒ Redis không tới được (kiểm tra `REDIS_URL`, mạng, Redis còn sống).
   - Không có `last_error`, tồn tăng ⇒ relay không chạy hoặc bị treo.
3. Lag cao nhưng không lỗi ⇒ relay quá tải (batch 100, poll 200 ms). Xem CPU/DB; cân nhắc chạy thêm relay (an toàn nhờ `SKIP LOCKED`, xem ADR-0013).

## Xử lý

- **Relay chết/treo**: khởi động lại. Không cần thao tác dữ liệu: event chưa publish được xử lý tiếp theo thứ tự `id`.
- **Redis chết**: khôi phục Redis. Relay tự backoff và publish lại; SSE hub ở API tự kết nối lại và gửi `resync` cho mọi client.
- **Cần phát lại tín hiệu** cho một khoảng (ví dụ client báo thiếu): an toàn vì client idempotent theo `(conversation_id, sequence)`.

  ```sql
  UPDATE outbox_events SET published_at = NULL
  WHERE id BETWEEN :from_id AND :to_id;
  ```

- **Không bao giờ xóa event chưa publish.** Dọn event đã publish cũ dùng `purge_published(older_than=7 ngày)`.

## Sau sự cố

- Ghi lại thời gian bắt đầu/kết thúc, `outbox_oldest_age_seconds` lớn nhất, nguyên nhân.
- Nếu do thiếu năng lực: mở việc đo tải và cân nhắc `pg_notify` / nhiều relay.
