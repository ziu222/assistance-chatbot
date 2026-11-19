# ADR-0018: Realtime — SSE mang tín hiệu, REST là sự thật

- Status: accepted
- Date: 2026-11-20

## Context

Nhân viên và khách cần thấy tin mới ngay. Kết nối realtime luôn có lúc đứt (mạng, deploy, Redis), và Redis pub/sub là at-most-once.

## Decision

- **SSE chỉ mang tín hiệu** `{t, c, s}` (loại sự kiện, conversation, sequence) với `id = outbox_events.id`; không mang nội dung tin.
- **REST là nguồn sự thật**: nhận tín hiệu ⇒ refetch `after_sequence`; kết nối (lại), `resync`, tab hiện lại ⇒ refetch toàn bộ phần đang xem; SSE chết hẳn ⇒ polling 30 s.
- **Hub trong mỗi API process**: một subscriber Redis `psubscribe evt:*`, hàng đợi có biên cho từng kết nối; client chậm ⇒ `resync` rồi đóng; Redis rớt ⇒ kết nối lại + `resync` cho mọi client.
- Heartbeat 15 s, thời lượng stream tối đa 30 phút (client kết nối lại ⇒ xác thực lại), giới hạn 3 stream/visitor, 20 stream/tenant.
- Không replay theo `Last-Event-ID` ở bước này (id vẫn phát để thêm sau không đổi định dạng).

## Alternatives considered

- WebSocket: hai chiều không cần thiết (gửi tin đã có REST idempotent), khó qua proxy hơn, phải tự làm reconnect.
- Đẩy nội dung tin qua SSE: phải đảm bảo giao đủ/đúng thứ tự trên kênh at-most-once; lộ PII qua Redis/log.

## Consequences

- Mất tín hiệu không bao giờ mất tin (E2E: chặn SSE, khách nhắn, mở lại ⇒ tin hiện qua refetch).
- Thêm một vòng REST sau mỗi tín hiệu (chấp nhận được ở quy mô SMB; các refetch chồng nhau được gộp).
- Giới hạn kết nối đếm theo process (N instance ⇒ N× giới hạn).

## Conditions to revisit

- Tải refetch đáng kể: gửi kèm diff nhỏ trong tín hiệu, hoặc replay theo `Last-Event-ID` từ outbox.
- Nhiều instance cần giới hạn chính xác: đếm kết nối qua Redis.
