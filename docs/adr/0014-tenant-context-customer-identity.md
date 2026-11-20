# ADR-0014: Tenant context và định danh khách

- Status: accepted
- Date: 2026-11-20

## Context

Hệ thống multi-tenant: rò dữ liệu chéo tenant là lỗi nghiêm trọng nhất. Widget công khai trên Internet, nên mọi thứ browser gửi lên đều không tin được. Bot có thể mở widget hàng loạt để tạo rác DB.

## Decision

- **Tenant không bao giờ lấy từ client.** Widget: `public_key → channel_connection → tenant` do server resolve; sau đó tenant/connection/visitor chỉ lấy từ token đã ký. Nhân viên: tenant lấy từ session DB.
- **Mọi repository bắt buộc `TenantContext`**; mọi truy vấn lọc `tenant_id`.
- **FK theo cặp `(tenant_id, id)`**: DB từ chối tham chiếu chéo tenant kể cả khi code sai.
- **Customer tạo lười**: khi có tin đầu tiên, không phải khi mở widget (`sessions` không ghi DB). Định danh kênh `channel_identities (tenant, connection, scoped_external_id)` unique; race tạo đồng thời: bên thua xóa customer của mình và dùng bản thắng.
- Conversation của tenant khác trả **404**, không phải 403 (không lộ sự tồn tại).

## Alternatives considered

- Row Level Security ngay từ đầu: mạnh hơn nhưng phức tạp khi test/migration; để làm lớp thứ hai sau (`tenant_id` đã có ở mọi bảng).
- Tạo customer khi mở widget: đơn giản hơn nhưng mở cửa cho rác DB.

## Consequences

- Test cross-tenant tự động cho mọi endpoint nhân viên; test "mở N session ⇒ 0 hàng DB".
- Visitor mất storage (xóa dữ liệu trình duyệt) ⇒ thành customer mới; merge customer là việc của bước sau.

## Conditions to revisit

- Có yêu cầu kiểm toán/tuân thủ chặt hơn: bật RLS với `SET LOCAL app.tenant_id`.
- Có kênh định danh mạnh (Zalo, email): cần luồng merge customer.
