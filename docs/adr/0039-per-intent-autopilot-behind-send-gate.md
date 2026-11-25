# ADR-0039: Autopilot theo từng ý định, sau một cổng gửi bằng code, tự hạ cấp khi có dấu hiệu sai

- Status: proposed (thiết kế `docs/design/06-autopilot.md`, chưa implement)
- Date: 2026-11-25

## Context

Bước 3 đo được ý định nào bot gần như không sai (mức sẵn sàng theo ý định, thiết kế 04 §7). Bước 4 cho nhân viên dùng nháp, và nhiều ý định được gửi nguyên văn gần như mọi lần. Nhân viên vẫn phải bấm gửi cho từng tin.

Từ đây, một nháp sai không còn chỉ tốn vài giây của nhân viên mà đến thẳng tay khách, có thể là một giá sai mà shop phải giữ lời. ADR-0033 đã quyết rằng mọi tin tự động đi qua một cổng duy nhất trong module `outbound`, và tới nay cổng đó chỉ mở cho tin do chủ shop viết. `messages.sender_type` chưa có giá trị cho AI.

## Decision

1. **Autopilot là đặc quyền theo ý định**, không phải một mode của agent. Agent ở `learning`; bảng `autopilot_intents` ghi ý định nào đang tự gửi. Chủ shop bật, server kiểm lại mức sẵn sàng với phiên bản cấu hình hiện tại. Ý định chuyển người, `order_intent` và `other` không bật được.
2. **Một cổng thuần `send_decision`, mặc định chặn**, trong `outbound`: tenant bật gửi, mọi ý định của lượt đã bật, nháp `grounded` và qua `validate_draft`, không cần người, `handling = bot`, không có tin mới hơn, chưa vượt số tin bot liên tiếp, kênh cho phép. Không có cờ bỏ qua.
3. **Bị chặn thì lùi về Learning**: nháp hiện cho nhân viên như bước 4.
4. **Kiểm lại dưới khóa hội thoại** ngay trước khi ghi; gửi idempotent theo `ai:<draft_id>`. Tin mang `sender_type = 'bot'` và `draft_id`.
5. **Theo dõi sau khi gửi** bằng nhân viên đính chính, phản ứng của khách, nhãn mẫu của chủ shop và kiểm số tiền sau gửi; **tự hạ cấp** ngay khi vượt ngưỡng hoặc có một lỗi số tiền.
6. **Đổi cấu hình AI (template, prompt, route model) thì phải đạt lại.** Đổi tri thức không hạ cấp, nhưng bản phát hành phải qua bộ ca chuẩn theo ý định cho mọi ý định đang Autopilot; hồi quy ⇒ hạ cấp ý định đó.

## Alternatives considered

- **Mode `autopilot` cho cả agent.** Đơn giản, nhưng một ý định yếu (báo giá) kéo cả agent xuống, hoặc buộc chủ shop bật cả những ý định chưa đủ tin cậy.
- **Tự bật khi đạt ngưỡng.** Nhanh hơn, nhưng chủ shop phải chịu trách nhiệm cho câu bot gửi; quyết định đó thuộc về họ.
- **Gửi sau một khoảng giữ để nhân viên hủy.** Giảm rủi ro nhưng khách vẫn chờ và nhân viên vẫn phải canh màn hình; Learning đã là dạng "duyệt trước". Để lại làm cấu hình nếu tenant thật cần.
- **Cho LLM tự đánh giá độ tự tin để quyết định gửi.** Không kiểm được bằng code và dễ bị chèn lệnh; cổng chỉ dựa trên dữ liệu đã kiểm.

## Consequences

- Migration nới `messages.sender_type` thêm `bot`, có review riêng; thêm `messages.draft_id`, `autopilot_intents`, `ai_reply_followups`, quyền `autopilot.manage`.
- Agent worker có thêm `AutopilotStage` sau runner; agent runtime vẫn không import `outbound`.
- Bước 3 không chấm được tin bot đã gửi (không có câu nhân viên); chất lượng ở Autopilot được đo bằng tín hiệu sau gửi và nhãn mẫu.
- Cổng eval của bước 4 phải có bộ ca chuẩn theo ý định trước khi Autopilot chạy lâu dài.

## Conditions to revisit

- Tỉ lệ bị chặn `stale` cao (khách nhắn nhiều tin liền) ⇒ gom tin lâu hơn trước khi chạy agent.
- Hạ cấp liên tục do đính chính của nhân viên mà nhãn chủ shop vẫn "đúng" ⇒ tín hiệu đính chính quá nhạy, cần lọc.
- Tenant cần gửi qua kênh sàn ⇒ cổng cần thêm quy định của từng sàn.
