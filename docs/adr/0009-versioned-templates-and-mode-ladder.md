# ADR-0009: Template có version trong repo, agent version bất biến, mode theo bậc

- Status: accepted
- Date: 2026-11-23 (ghi lại quyết định đã áp dụng từ bước 2.2)

## Context

Mỗi shop cần một agent khởi đầu từ cấu hình đã kiểm chứng (intent, hướng dẫn, tool, ngân sách), và mọi run phải truy ngược được về đúng cấu hình đã dùng. Nếu template được sửa tại chỗ, một thay đổi sẽ âm thầm đổi hành vi của mọi agent đang chạy và làm sai lệch so sánh eval giữa các lần.

## Decision

1. **Template là file TOML có version trong repo** (`agent_management/templates/<key>.v<n>.toml`), đọc bằng `tomllib` của thư viện chuẩn và validate bằng Pydantic khi nạp; file sai thì lỗi ngay lúc khởi động. Thay đổi template đi qua review PR như code. Không có UI quản lý template.
2. **Tạo agent ⇒ chụp toàn bộ nội dung template vào `agent_versions.config`** (bất biến). Template lên v2 không ảnh hưởng agent cũ. Sửa cấu hình agent = tạo version mới; run luôn ghi `agent_version_id`.
3. **Template tham chiếu route LLM** (`classify`, `draft`), không tham chiếu tên model (ADR-0015).
4. **Mode theo bậc an toàn:** bước 2 chỉ có `manual` và `shadow`. Mode được kiểm khi bắt đầu run **và** ngay trước khi lưu nháp, nên đổi mode giữa chừng không để lọt run cũ. Hai admin sửa cùng lúc được chặn bằng `agents.version` (compare-and-set).
5. **Kill switch cấp tenant mặc định tắt** (`tenant_ai_settings.ai_enabled = false`).

## Consequences

- Rollback cấu hình agent là trỏ lại version cũ; eval so sánh được giữa các version.
- Thêm một bậc mode (learning, autopilot) là quyết định riêng ở bước sau, kèm cổng gửi (ADR-0033).
- Chủ shop không tự tạo template; họ tùy biến qua tri thức, chính sách hội thoại và giọng thương hiệu.
