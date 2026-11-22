# ADR-0033: Một cổng gửi duy nhất cho tin tự động

- Status: accepted
- Date: 2026-11-23

## Context

Tới bước 2.8, hệ thống chưa gửi gì tự động cho khách: output của agent chỉ nằm ở `agent_drafts`, còn tin tự động của tầng chính sách (ADR-0031) chỉ được ghi lại. Chủ shop cần tin chào, tin ngoài giờ và tin tạm nghỉ tới được khách thật.

Mở đường gửi là thay đổi rủi ro nhất của bước 2. Nếu nhiều module tự ghi tin outbound, sẽ không có chỗ duy nhất để kiểm tra điều kiện, và không chặn được việc agent runtime vô tình gửi output của LLM.

## Decision

1. **Module `outbound` là nơi duy nhất ghi tin tự động.** `send_block(GateInput)` là hàm thuần trả lý do bị chặn hoặc `None`; `send_canned` ghi tin trong transaction của tầng chính sách, vốn đang giữ khóa `FOR UPDATE` trên hội thoại.
2. **Bước 2.9 chỉ mở cổng cho tin do chủ shop viết**: nội dung nguyên văn từ `policy_release`, chỉ thay `{shop_name}` và `{next_open}`. Nháp của agent vẫn không có đường tới khách; bước 5 mới mở cổng cho nháp `grounded` theo từng intent.
3. **Điều kiện để gửi**: shop đã bật công tắc gửi (`tenant_policy.auto_send`, mặc định tắt, và lưu cấu hình không tự bật); hội thoại đang mở; bot đang giữ hội thoại lúc quyết định; kênh gửi chủ động được (hiện chỉ webchat, Messenger/Zalo phải kiểm cửa sổ 24 giờ trước). Bị chặn thì lý do được ghi vào `policy_decisions.send_block` và hiện trong trace.
4. **Tin gửi là `sender_type='system'`.** Giá trị này có sẵn trong CHECK của `messages`, nên không cần nới schema. Tin không đổi `handling`, không tăng `unread_count`, và phát `message.created` cùng dạng tin của nhân viên để widget tải về. Khóa idempotency là `policy:<conversation>:<to_sequence>`, nên worker chạy lại cũng không gửi trùng.
5. **Test kiến trúc**: SQL thô ghi `messages` chỉ được nằm trong `outbound`; `agent_runtime` không được import `outbound`; `outbound` chỉ ghi `'system'`.

## Consequences

- Muốn bật thêm loại gửi tự động nào cũng phải sửa `send_block` và test của nó: mọi điều kiện nằm ở một chỗ.
- Agent chạy sau khi tin tự động đã được ghi. Nó thấy tin đó trong lịch sử (vai shop) và không bị `superseded`, vì bước kiểm tra lúc lưu so với `last_sequence` đọc khi dựng ngữ cảnh.
- Công tắc nằm ở `tenant_policy` chứ không ở `tenant_ai_settings` như thiết kế ban đầu: tin tự động không phụ thuộc AI, và công tắc chỉ có nghĩa khi đã có cấu hình để gửi.
- Kênh khác webchat bị chặn (`channel_not_supported`) cho tới khi kiểm xong chính sách nhắn tin của nền tảng.
