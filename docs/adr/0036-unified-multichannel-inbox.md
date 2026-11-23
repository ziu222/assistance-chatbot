# ADR-0036: Mọi kênh bán hàng đổ về một pipeline và một inbox

- Status: proposed (thiết kế `docs/design/03-order-flow-and-unified-inbox.md`, chưa implement)
- Date: 2026-11-24

## Context

Shop nhỏ bán cùng lúc qua website, Zalo, Facebook, Shopee, TikTok Shop, và mỗi nơi là một app riêng để trả lời khách. Chủ shop muốn một chỗ để thấy mọi khách và mọi yêu cầu đặt hàng.

Bước 1 đã thiết kế pipeline độc lập với kênh: tin vào được chuẩn hóa thành `NormalizedChannelEvent`, gắn với `channel_connections` / `channel_identities`, rồi đi qua cùng `ReceiveInboundMessage`, outbox, chính sách hội thoại, agent và ticket. Hiện chỉ có webchat.

Mỗi kênh có giới hạn riêng: khoảng thời gian được phép trả lời sau tin cuối của khách, có hỗ trợ link hay nút bấm không, độ dài tin. Gửi tin vi phạm giới hạn thì bị kênh từ chối, hoặc nặng hơn là bị khóa trang.

## Decision

1. **Mỗi kênh là một adapter hai chiều.**
   - Chiều vào: xác minh chữ ký webhook, chống trùng theo id tin của kênh, chuyển thành `NormalizedChannelEvent`.
   - Chiều ra: `ChannelSender`.
   - Không có logic nghiệp vụ trong adapter; chính sách, agent, ticket và phiếu đặt hàng dùng chung cho mọi kênh.
2. **Mỗi kênh khai báo `ChannelCapabilities`:** `reply_window`, `supports_links`, `supports_buttons`, `max_text_length`.
   - `SendGate` và màn hình soạn tin của nhân viên đọc các giới hạn này và chặn trước khi gửi.
   - Con số của từng kênh lấy từ tài liệu chính thức lúc implement.
3. **Không tự gộp danh tính khách giữa các kênh.** Mỗi kênh là một khách; nhân viên liên kết bằng tay khi chắc chắn.
4. **Quy trình đặt hàng ghi đè được theo kênh** (`OrderFlowConfig.by_channel`, ADR-0035).
5. **Token kết nối được mã hóa khi lưu**, mỗi kết nối có OAuth riêng, không bao giờ log token.
6. **Thứ tự kênh:** Zalo OA hoặc Facebook Messenger trước; chat của sàn sau, vì cần duyệt ứng dụng đối tác.

## Alternatives considered

- **Dùng nền tảng gom kênh của bên thứ ba.** Nhanh có nhiều kênh, nhưng phụ thuộc vào bên ngoài về giá, dữ liệu khách và độ trễ, và vẫn phải tự làm chính sách hội thoại, agent, ticket. Có thể dùng làm một adapter nếu cần, nhưng không thay pipeline.
- **Tự gộp khách theo số điện thoại hoặc tên.** Tiện cho nhân viên, nhưng gộp sai thì lộ hội thoại của người này cho người khác. Chưa có lợi ích nào đủ lớn để chấp nhận rủi ro đó.
- **Một agent riêng cho mỗi kênh.** Nhân đôi cấu hình, tri thức và eval. Khác biệt giữa các kênh đã được tách ra ở `ChannelCapabilities` và `by_channel`.

## Consequences

- `channel_connections.provider` mở rộng và thêm `credentials` đã mã hóa; inbox có huy hiệu và bộ lọc kênh.
- `SendGate` phải biết kênh của hội thoại; test hợp đồng cho từng adapter dùng phản hồi ghi sẵn theo tài liệu chính thức, CI không gọi mạng.
- Có thêm webhook công khai, mỗi kênh một đường; xác minh chữ ký sai thì trả lỗi và không ghi gì.
- Không có gộp khách nên báo cáo "số khách" đếm theo kênh. Cần ghi rõ điều này trên màn hình thống kê.

## Conditions to revisit

- Nhân viên liên kết tay quá nhiều (ví dụ trên 10% khách có từ hai kênh) ⇒ cân nhắc gợi ý gộp kèm xác nhận.
- Kênh có chính sách mới về tin tự động hoặc khoảng thời gian được trả lời ⇒ cập nhật `ChannelCapabilities` và test hợp đồng của kênh đó.
