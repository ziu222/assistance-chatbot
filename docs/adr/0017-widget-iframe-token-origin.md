# ADR-0017: Widget — iframe, session token, origin

- Status: accepted
- Date: 2026-11-20

## Context

Widget được nhúng vào website của khách hàng (SMB) mà ta không kiểm soát: CSS/JS của họ có thể phá giao diện hoặc đọc dữ liệu chat; cookie bên thứ ba trong iframe bị trình duyệt chặn; `EventSource` không gửi được header.

## Decision

- **Loader nhỏ + iframe**: `loader.js` (không dependency) tạo nút nổi trong **Shadow DOM đóng** và iframe `/w/{public_key}` do backend phục vụ. `postMessage` chỉ để mở/đóng, kiểm tra cả `source` lẫn `origin`; **token không bao giờ đi qua postMessage**.
- **Session token JWT HS256**, thuật toán cố định (chặn `alg=none`/đổi thuật toán), `exp` bắt buộc, xoay khóa bằng `kid`, secret ≥ 32 byte, không có secret mặc định. Lưu trong `localStorage` của origin iframe.
- **Allowlist origin có hiệu lực qua `frame-ancestors`** ở trang iframe (CSP động theo connection; allowlist rỗng ⇒ 404). Giá trị allowlist chỉ nhận dạng `scheme://host[:port]` để không chèn được directive CSP.
- **API public chỉ nhận `Origin` = origin của chính iframe** (`PUBLIC_ORIGIN`).
- Trang iframe chỉ tải file cùng origin, CSP `default-src 'self'` không `unsafe-inline`; nội dung tin chỉ render dạng text.
- SSE của khách đi qua `fetch` stream + header `Authorization` (token không nằm trên URL).

## Alternatives considered

- Nhúng thẳng vào DOM trang host (không iframe): bị CSS/JS host ảnh hưởng, token lộ cho script của host.
- Cookie phiên trong iframe: bị chặn bởi chính sách cookie bên thứ ba.
- Kiểm tra `Origin` theo allowlist ở API (thiết kế ban đầu): **không dùng được** — request từ iframe mang origin của iframe, không phải của site host. Phát hiện khi làm bước 1.5.

## Consequences

- Origin **không phải ranh giới bảo mật** (client ngoài trình duyệt giả được header); ranh giới thật là token ký + rate limit nhiều tầng.
- Storage bị phân vùng theo site nhúng ⇒ mỗi site có `visitor_id` riêng (chấp nhận, có lợi cho cô lập).
- Đã kiểm thủ công + E2E: CSS "phá" của host không ảnh hưởng widget; nhúng từ origin ngoài allowlist bị trình duyệt chặn.

## Conditions to revisit

- Cần thu hồi token tức thời cho từng visitor: thêm danh sách thu hồi hoặc TTL ngắn + refresh.
- Bundle React (~73 KB gzip) thành vấn đề: chuyển Preact.
