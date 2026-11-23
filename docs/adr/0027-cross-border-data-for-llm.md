# ADR-0027: Dữ liệu hội thoại gửi tới nhà cung cấp LLM ở nước ngoài

- Status: proposed — **phải chốt trước khi có khách thật**
- Date: 2026-11-23

## Context

Agent gửi nội dung hội thoại của khách tới nhà cung cấp LLM (Anthropic, OpenAI) có máy chủ ở nước ngoài. Theo `PROJECT_CONTEXT` §16.6, đây là chuyển dữ liệu cá nhân ra nước ngoài theo Luật Bảo vệ dữ liệu cá nhân (Luật 91/2025/QH15) và các văn bản hướng dẫn, kéo theo nghĩa vụ về căn cứ xử lý, thông báo, hồ sơ đánh giá tác động và thủ tục chuyển dữ liệu. ADR này **không** phải tư vấn pháp lý; nó ghi lại những gì kỹ thuật đã làm và những gì còn phải quyết.

## Decision (đang áp dụng ở bước 2)

1. **Chỉ chạy với dữ liệu giả/demo** (bộ shop giả lập, dữ liệu thử). Kill switch AI mặc định tắt cho mọi tenant.
2. **Tối thiểu hóa dữ liệu gửi đi:** chỉ gửi text hội thoại (tối đa 10 lượt gần nhất) và tri thức của shop. Không gửi tên thật, số điện thoại, địa chỉ của khách lấy từ hồ sơ khách. Không lưu prompt đầy đủ ở phía Atlas.
3. **Không dùng tính năng lưu trạng thái phía nhà cung cấp** (ADR-0030).
4. Trợ lý viết chỉ gửi chữ của chủ shop, không gửi dữ liệu khách.

## Còn phải quyết trước khi có khách thật

- Khu vực xử lý (nhà cung cấp, vùng dữ liệu, điều khoản lưu giữ và không huấn luyện trên dữ liệu).
- Che (mask) thông tin cá nhân trong nội dung khách tự gõ (số điện thoại, địa chỉ trong tin nhắn) trước khi gửi.
- Thủ tục pháp lý theo luật: căn cứ, thông báo cho khách qua widget, hồ sơ đánh giá tác động chuyển dữ liệu; cần người có chuyên môn pháp lý xác nhận.
- Quy trình xóa theo yêu cầu (đã có ở phía Atlas cho `messages`, `agent_drafts`).

## Consequences

- Cho tới khi ADR này chuyển sang accepted, không bật AI cho tenant có khách thật.
