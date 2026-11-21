# ADR-0008: Hàng đợi và xử lý nền cho agent

- Status: accepted
- Date: 2026-11-21

## Context

Bước 1 phát sự kiện qua Redis pub/sub — **at-most-once**: process nào mất kết nối thoáng qua là lỡ tín hiệu. Chấp nhận được cho UI (UI refetch REST), **không** chấp nhận được cho agent: lỡ tín hiệu = khách không bao giờ được AI xử lý. Agent còn cần (1) gom các tin liên tiếp của khách thành một lần chạy (debounce, `PROJECT_CONTEXT` §9.2.1) và (2) không bao giờ có hai run song song trên cùng hội thoại (§14.5).

## Decision

- **Hàng đợi trên Postgres**, bảng `conversation_run_requests`, **một hàng mỗi hội thoại** (PK `conversation_id`).
- **Enqueue trong transaction của relay**: relay nhận hook `on_published`; với `message.created` inbound nó upsert hàng run request **cùng transaction** đánh dấu `published_at`. Không bao giờ có trạng thái "đã publish mà agent chưa nhận". Upsert idempotent và không lùi `to_sequence` ⇒ replay outbox an toàn.
- **Debounce bằng cột `due_at`**: mỗi tin mới đẩy `due_at = min(now + window, first_seen_at + max_wait)` (khởi điểm 6 s / 20 s).
- **Claim bằng `FOR UPDATE SKIP LOCKED` + lease**: worker đặt `lease_until` và một `lease_token` mới rồi commit ngay; run (gọi model, lâu) chạy ngoài transaction. Handler bị giới hạn ở 80% thời hạn lease.
- **Fencing**: mọi lệnh kết thúc (`complete`, `retry_later`) đều có điều kiện `lease_token = token của mình`. Worker có lease đã hết hạn không ghi được gì (`lost`).
- **Tin mới trong lúc chạy**: `complete` không xóa hàng mà dời `from_sequence` sang phần chưa xử lý và mở cửa sổ gom mới.
- **Lỗi tạm thời**: backoff lũy thừa có jitter, tối đa `agent_max_attempts` lần rồi bỏ (không kẹt hàng đợi).
- Run request **không** lưu agent: worker xác định agent/mode lúc claim (đổi cấu hình trong lúc chờ debounce thì dùng cấu hình mới nhất).

## Alternatives considered

- **Redis Streams + consumer group**: bền hơn pub/sub, nhưng thêm một nguồn sự thật thứ hai cần đồng bộ với Postgres, và debounce/khóa theo hội thoại phải tự làm thêm.
- **Celery/Dramatiq**: job theo tin nhắn ⇒ khó gom tin và khó bảo đảm một run/hội thoại; thêm broker và vận hành.
- **Consumer đọc thẳng outbox theo con trỏ `id`**: `id` identity được cấp lúc INSERT nhưng commit có thể theo thứ tự khác ⇒ con trỏ có thể nhảy qua event chưa commit (mất việc).

## Consequences

- Không thêm hạ tầng; mọi bất biến kiểm chứng bằng test trên Postgres thật (gom 3 tin ⇒ 1 hàng; 3 worker song song ⇒ mỗi hội thoại đúng 1 lần; lease hết hạn ⇒ worker cũ bị chặn ghi; replay event cũ không lùi `to_sequence`). Mutation check: bỏ SKIP LOCKED, fencing, `GREATEST`, trần `max_wait` hoặc điều kiện "không có tin mới" đều làm test fail.
- Mọi tin khách đều tạo một hàng, kể cả tenant chưa bật AI; worker bỏ nhanh. Chấp nhận ở quy mô hiện tại.
- Thông lượng bị giới hạn bởi poll (`idle_sleep` 0,5 s) và số vòng song song; đủ cho SMB.

## Conditions to revisit

- Hàng đợi lớn tới mức poll tốn đáng kể: thêm `pg_notify` đánh thức worker, hoặc lọc tenant bật AI ngay khi enqueue.
- Cần ưu tiên (khách VIP, kênh trả phí): thêm cột ưu tiên vào `ORDER BY`.
- Có job nền không gắn hội thoại (eval, sync catalog): bảng job riêng cùng kỹ thuật, không nhồi vào bảng này.
