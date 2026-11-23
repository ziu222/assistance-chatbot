# ADR-0035: Quy trình đặt hàng là cấu hình có phiên bản; bot ghi phiếu, không tự chốt đơn

- Status: accepted (O2 đã implement; xem `docs/design/03-order-flow-and-unified-inbox.md`)
- Date: 2026-11-24

## Context

Khi khách nhắn "em muốn đặt X", template hiện tại chuyển người ngay sau bước phân loại (`order_intent` nằm trong `handoff_intents`). Bot không tra kho, không soạn nháp, và nhân viên phải đọc lại cả cuộc trò chuyện. Eval lộ ra điều này qua các ca "em muốn đặt X, shop có trả lời được không?": bot chuyển người còn bộ dữ liệu ghi là không, và cả hai đều không giúp gì cho nhân viên.

Mỗi shop chốt đơn một kiểu: gửi link sản phẩm, xin tên/số điện thoại/địa chỉ rồi nhân viên gọi, chỉ nhận chuyển khoản trước, chốt trên sàn. Cùng một shop cũng có thể chốt khác nhau theo kênh. Viết quy trình thành chữ tự do trong prompt thì code không biết còn thiếu thông tin gì, không kiểm tra được, và không đo được bằng eval.

Tạo đơn thật là thao tác có tác dụng phụ, cần dữ liệu cá nhân và tổng tiền đúng (phí ship, khuyến mãi), trong khi nhiều shop chưa viết những thứ đó. Thiết kế bước 2 đã cố ý loại tool có tác dụng phụ.

## Decision

1. **`OrderFlowConfig` có cấu trúc, theo từng tenant:**
   - trường bắt buộc, chọn từ danh sách cố định;
   - cách thanh toán;
   - `destination` (`staff_confirms` / `product_link` / `marketplace_link` / `website_checkout`);
   - câu thanh toán và câu xác nhận do chủ shop viết, dùng nguyên văn;
   - ghi đè theo kênh (`by_channel`).

   Phiên bản theo mẫu `policy_releases`: bản phát hành bất biến cùng một con trỏ; khôi phục bằng cách chuyển con trỏ.
2. **Bot ghi `order_requests`, không tạo hay xác nhận đơn.**
   - Phiếu đi `collecting → ready → confirmed | cancelled | expired`, mỗi hội thoại có tối đa một phiếu đang mở.
   - `confirmed` do nhân viên bấm.
   - Phiếu `ready` mở ticket nhờ nhân viên với `source = 'order'`.
3. **Code quyết định trường còn thiếu.**
   - Đơn giá lấy từ catalog; số điện thoại tách bằng code.
   - Các trường do model trích được đánh dấu "chưa xác minh".
   - Model chỉ soạn câu hỏi cho trường tiếp theo.
4. **Kiểm tra bằng code chặn câu "đơn đã đặt/xác nhận"**, giống chặn số tiền bịa.
5. **Dữ liệu cá nhân chỉ ở phiếu.**
   - Không vào `learning_signals`, outbox, SSE hay trace đã chiếu.
   - Bị xóa theo thời hạn: 30 ngày với phiếu hủy hoặc quá hạn, 180 ngày với phiếu đã xác nhận; chủ shop chỉnh được.
6. **"Bot học quy trình" là đề xuất kèm duyệt.**
   - Trợ lý viết điền sẵn cấu hình từ FAQ/tài liệu.
   - Tín hiệu `order_confirmed` (đã chiếu) gợi ý chỉnh trường bắt buộc.
   - Chủ shop phát hành; không có gì tự áp dụng.
7. **Template v4** bỏ `order_intent` khỏi `handoff_intents`. Trong lúc thu thập, bot tự hỏi tiếp; phiếu đủ thông tin thì nháp có `needs_human = true`, `reason = order` và ticket được mở.

## Alternatives considered

- **Giữ chuyển người ngay khi có ý định đặt hàng.** An toàn nhưng bỏ phí phần bot làm tốt (tra kho, gợi ý), và nhân viên mất thời gian đọc lại.
- **Quy trình viết bằng chữ tự do trong prompt.** Linh hoạt nhất, nhưng không có cách biết bot đã hỏi đủ chưa, không chặn được hứa hẹn sai, và mỗi lần sửa chữ là một lần prompt đổi không kiểm soát.
- **Bot tạo đơn qua tool.** Giảm việc cho nhân viên nhất, nhưng cần dữ liệu cá nhân, tổng tiền đúng và tích hợp với nơi shop quản lý đơn. Một lỗi ở đây là đơn sai thật. Để lại cho giai đoạn "bot đề xuất đơn, nhân viên duyệt một chạm" (O6), sau khi có số đo từ phiếu.
- **Model tự quản lý trạng thái phiếu.** Model không nhớ gì giữa các run (ADR-0030); trạng thái phải nằm trong DB và do code tính.

## Consequences

- Thêm các bảng `order_flow_releases`, `tenant_order_flow`, `order_requests`, giá trị `'order'` cho `handoff_requests.source`, cột `catalog_items.url`.
- Prompt có thêm khối `<order_flow>`, thay đổi theo hội thoại, nên đặt sau khối tri thức ổn định (ADR-0028).
- Output nháp có thêm `order_fields`. Kiểm tra nháp phải cho phép link, nhưng chỉ những URL có trong kết quả tool của chính run đó.
- Eval cần cấu hình quy trình cho các shop giả lập, cùng nhóm nhiệm vụ đặt hàng và các chiều chấm mới.
- Atlas giữ dữ liệu cá nhân của khách lâu hơn hiện nay; phải có cơ chế xóa theo hạn và che số điện thoại trong danh sách.

## Conditions to revisit

- Tỉ lệ nhân viên sửa trường do bot tách cao (ví dụ trên 20%) ⇒ thu hẹp những gì bot tự điền.
- Shop cần bot tạo đơn thật ⇒ thiết kế approval request (bước 5) trước khi thêm tool có tác dụng phụ.
- Quy định pháp lý về dữ liệu cá nhân thay đổi ⇒ xem lại thời hạn lưu và nơi lưu.
