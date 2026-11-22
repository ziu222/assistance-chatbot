# ADR-0031: Tầng chính sách hội thoại xác định chạy trước agent

- Status: accepted
- Date: 2026-11-23

## Context

Chủ shop muốn tự quyết định: khách nhắn lần đầu thì chào bằng câu gì, ngoài giờ hay lúc tạm nghỉ thì trả lời ra sao, sau câu đó bot có tư vấn tiếp hay chờ nhân viên. Người dùng là chủ shop nhỏ, không rành kỹ thuật: một rule engine (điều kiện, thứ tự, xung đột) sẽ không dùng được và dễ tạo cấu hình mâu thuẫn.

Cùng lúc, hệ thống phải tránh việc bot và nhân viên cùng trả lời một hội thoại, và mọi thứ tự động phải truy ngược được về đúng cấu hình đã dùng.

Khảo sát thị trường (Intercom Fin, Zendesk, Agentforce, Sierra, Decagon và các nền tảng Việt Nam) cho thấy các hệ thống lớn tách phần **quyết định** (ai được nói, khi nào chuyển người) khỏi phần **LLM viết chữ**. Các nền tảng Việt Nam được khảo sát không có tài liệu công khai về cấu hình giờ làm việc.

## Decision

1. **Một tầng chính sách chạy trước `AgentRunner`** trong agent worker (`PolicyStage`). Nó gọi hàm thuần `decide(config, state, now) -> Decision` rồi ghi kết quả trong một transaction: một hàng `policy_decisions` cho mỗi cụm tin đã gom (unique theo `conversation_id, to_sequence`, nên worker retry không quyết định hai lần), và đổi `conversations.handling` nếu cần.
2. **Bộ tình huống cố định, ưu tiên nằm trong code**: tạm nghỉ > ngoài giờ > chào khách > tư vấn thường. Mỗi lượt có tối đa một tin tự động. Tình huống áp dụng nhưng bị tắt thì không rơi xuống tình huống thấp hơn. Chủ shop chỉ bật/tắt, viết nội dung (biến `{shop_name}`, `{next_open}`) và chọn "bot tư vấn tiếp" hay "chờ nhân viên". Thêm tình huống mới phải qua PR.
3. **Giờ làm việc là dữ liệu trong cấu hình**, tính bằng `zoneinfo` (cần gói `tzdata` trên Windows và image tối giản): nhiều khoảng mỗi ngày, khoảng vắt qua nửa đêm, ngày nghỉ, "tạm nghỉ đến…" tự hết hạn.
4. **Cấu hình có version**: mỗi lần lưu tạo một `policy_releases` bất biến, và `tenant_policy.current_release_id` trỏ tới release đó. Khôi phục chỉ là đổi con trỏ. Mỗi quyết định ghi `policy_release_id`.
5. **`conversations.handling ∈ {bot, waiting_human, human}`**. Nhân viên gửi tin ⇒ `human`, ghi trong cùng transaction với tin nhắn. Trả lại cho bot chỉ bằng nút tường minh (compare-and-set theo `conversations.version`). Cổng của agent runtime đọc `handling` và ghi run `skipped` (`handling_human` / `handling_waiting`), nên trace vẫn cho thấy vì sao bot im.
6. **Trình mô phỏng gọi đúng `decide`**: `POST /api/v1/policy/simulate` không ghi gì.
7. **Bước 2.7 chỉ ghi tin tự động** (`policy_decisions.sent = false`, hiện trong trace là "lẽ ra sẽ gửi"). Việc gửi thuộc về module `outbound` qua một cổng duy nhất ở bước 2.9 (ADR-0033).

## Consequences

- Hành vi "khi nào nói gì" được test bằng bảng tình huống và trình mô phỏng, không phụ thuộc LLM.
- Chủ shop không diễn đạt được logic tùy ý (ví dụ "chỉ chào khách mua lần đầu"). Đây là đánh đổi có chủ đích; nhu cầu mới sẽ thành tình huống mới.
- "Một lần mỗi lần tạm nghỉ" đang được xấp xỉ bằng khoảng chào lại (`gap_hours`). Nếu shop tạm nghỉ hai lần sát nhau, cần lưu thời điểm bật tạm nghỉ.
- Giờ trong giao diện được nhập và hiển thị theo giờ máy người dùng, chỉ đúng khi máy ở cùng múi giờ với shop.
- Thêm một bước đọc/ghi DB trước mỗi run. Chi phí nhỏ so với một lần gọi LLM.
