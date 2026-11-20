# ADR-0029: Auth nhân viên tối thiểu

- Status: accepted
- Date: 2026-11-20

## Context

Admin inbox cần đăng nhập ngay ở bước 1. Chưa có quyết định về IdP (câu hỏi mở 24.2). Cần đúng chuẩn bảo mật nhưng không xây quá mức.

## Decision

- Email + mật khẩu băm **argon2id**; email không tồn tại vẫn verify với hash giả (không lộ email qua thời gian phản hồi); một thông báo lỗi chung.
- **Session lưu DB**: giá trị ngẫu nhiên 256-bit, DB chỉ lưu sha256. Cookie `__Host-sid; HttpOnly; Secure; SameSite=Lax; Path=/`, hết hạn tuyệt đối 12 giờ, logout xóa dòng. Session bị từ chối khi user/membership/tenant bị tắt.
- **CSRF**: SameSite=Lax + header `X-Requested-With` bắt buộc + `Origin` = `STAFF_ORIGIN` cho mọi method không an toàn, kể cả login.
- Rate limit login theo IP và theo email, đếm **trước** khi verify, fail closed khi Redis lỗi.
- `authorize(actor, action, tenant)` tồn tại ngay dù mới một luật, để RBAC thay vào không đổi chỗ gọi.
- Seed CLI sinh mật khẩu ngẫu nhiên in **một lần**; không có mật khẩu mặc định.

## Alternatives considered

- JWT cho nhân viên: khó thu hồi, phải lưu ở client.
- IdP ngoài (OIDC) ngay: chưa có yêu cầu, thêm phụ thuộc vận hành.

## Consequences

- Không có quên mật khẩu, MFA, mời thành viên: là việc của bước sau.
- `audit_events` chưa có (nợ có chủ đích; bắt buộc từ bước 2).

## Conditions to revisit

- Khách hàng doanh nghiệp yêu cầu SSO: thêm OIDC, giữ session DB làm lớp phiên.
- Cần MFA hoặc chính sách mật khẩu theo tenant.
