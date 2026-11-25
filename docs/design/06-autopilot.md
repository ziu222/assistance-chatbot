# Thiết kế bước 5: Autopilot: bot tự trả lời theo từng ý định đã đủ tin cậy

- Trạng thái: **A1–A3 đã implement** (A1: migration `sender_type = 'bot'` + `messages.draft_id`, `send_decision`, `outbound.send_ai_reply`; A2: `autopilot_intents`, quyền `autopilot.manage`, API bật/tắt + nút dừng `/pause`; A3: `AutopilotStage` sau `AgentRunner`, gửi webchat dưới khóa, lùi Learning khi bị chặn, nhãn "Trợ lý tự động" trong widget và màu riêng trong inbox); A4–A6 là thiết kế. Quyết định chính: [ADR-0039](../adr/0039-per-intent-autopilot-behind-send-gate.md).
- Ngày: 2026-11-25
- Liên quan:
  - [ADR-0033](../adr/0033-single-send-gate-for-automatic-messages.md) (một cổng gửi duy nhất cho tin tự động, module `outbound`);
  - [02-agent-from-template.md](02-agent-from-template.md) (E8 provenance, `validate_draft`, §12 tầng chính sách và `handling`);
  - [03-order-flow-and-unified-inbox.md](03-order-flow-and-unified-inbox.md) (O6: bot đề xuất đơn, người duyệt);
  - [04-shadow-comparison.md](04-shadow-comparison.md) (§7 mức sẵn sàng theo ý định);
  - [05-learning.md](05-learning.md) (`draft_uses`, bản phát hành tri thức, cổng eval và giới hạn của nó).

## 1. Vấn đề

Sau bước 4, nhân viên vẫn bấm gửi cho mọi tin, kể cả những câu bot đã trả lời đúng nguyên văn hàng trăm lần ("shop mở cửa mấy giờ", "ship về Đà Nẵng bao nhiêu"). Số đo bước 3 và 4 đã chỉ ra ý định nào bot gần như không sai. Bước 5 cho bot **tự gửi** đúng những ý định đó.

Rủi ro đổi bản chất: tới bước 4, một nháp sai chỉ tốn vài giây của nhân viên. Từ bước 5, một nháp sai đến thẳng tay khách, có thể là một giá sai mà shop phải giữ lời. Vì vậy Autopilot được thiết kế như một **đặc quyền theo từng ý định**: cấp khi có số đo, mất ngay khi số đo xấu đi, và mọi tin đều phải qua một cổng bằng code mà LLM không vượt được.

## 2. Mục tiêu và ngoài phạm vi

**Mục tiêu**

- Chủ shop bật tự trả lời **cho từng ý định**, chỉ khi ý định đó đạt mức sẵn sàng (thiết kế 04 §7) với phiên bản cấu hình hiện tại.
- Mọi tin bot gửi đi qua **một cổng gửi duy nhất** (`outbound`), quyết định bằng code từ dữ liệu đã kiểm: nháp có nguồn, không cần người, đúng ý định đã bật, hội thoại chưa có tin mới, kênh cho phép.
- Bị chặn ở cổng thì **lùi về Learning**: nháp hiện cho nhân viên như bước 4, khách không bị bỏ rơi.
- **Theo dõi sau khi gửi** và **tự hạ cấp** ý định khi có dấu hiệu sai, không chờ chủ shop phát hiện.
- Một nút dừng tất cả, có hiệu lực ngay với cả tin đang chờ gửi.

**Ngoài phạm vi (có chủ đích)**

- Tự chốt đơn hay tạo đơn. Luồng đặt hàng vẫn là bot đề xuất, nhân viên duyệt một chạm (O6), làm sau.
- Ý định chuyển người (`complaint`, `human_request`, `discount_request`) và `other`: không bao giờ bật được.
- Tool có side effect (đổi đơn, hoàn tiền, giữ hàng).
- Tự bật Autopilot. Hệ thống chỉ đề xuất; chủ shop bấm.
- Kênh sàn (Shopee, TikTok Shop): chưa có đường gửi qua API sàn.

## 3. Bất biến

1. **Chỉ module `outbound` ghi tin không phải của khách hay nhân viên.** Agent runtime vẫn không import `outbound` (test kiến trúc giữ nguyên). Một tầng riêng sau runner đọc nháp đã lưu rồi gọi cổng.
2. **Cổng là hàm thuần, mặc định chặn.** Thiếu bất kỳ điều kiện nào ⇒ không gửi, kèm lý do đọc được trong trace. Không có cờ nào bỏ qua cổng.
3. **Nháp chỉ được gửi khi tất cả ý định của lượt đều đã bật.** Khách hỏi "giờ mở cửa và giảm giá được không" ⇒ có `discount_request` ⇒ không gửi.
4. **Không gửi trên dữ liệu cũ.** Ngay trước khi ghi, dưới khóa hội thoại, kiểm lại: không có tin khách mới hơn nháp, không có tin nhân viên sau tin khách đó, `handling` vẫn là `bot`, Autopilot của ý định vẫn bật.
5. **Idempotent.** Mỗi nháp gửi tối đa một lần (`external_message_id = ai:<draft_id>`); worker chạy lại không gửi hai lần.
6. **Đổi cấu hình AI thì phải đạt lại.** Đổi template, phiên bản template, `PROMPT_VERSION` hay route model ⇒ ý định về lại Learning tới khi đạt mức sẵn sàng với cấu hình mới.
7. **Tin bot được đánh dấu.** `messages.sender_type = 'bot'`, kèm `draft_id` và `run_id`; trace đủ để trả lời "vì sao bot gửi câu này".

## 4. Bật Autopilot cho một ý định

### 4.1. Ai được bật, khi nào

- Ý định bật được: `greeting`, `faq`, `product_info` (ở A3); `price_quote` sau khi có kiểm độ mới catalog (A6). Không bao giờ: ý định trong `handoff_intents`, `order_intent`, `other`.
- Màn hình "Chấm nháp" đã có bảng mức sẵn sàng theo ý định (`GET /shadow/summary`). Thêm nút **Bật tự trả lời** cạnh ý định có `ready = true`. Ý định chưa đạt hiện lý do (ví dụ "chưa đủ mẫu 12/30").
- Bật dùng quyền mới `autopilot.manage` (`owner`). Server kiểm lại mức sẵn sàng lúc bật, không tin client. Lưu khóa cấu hình lúc đạt (`config_key`: template, phiên bản, `PROMPT_VERSION`) và nguồn đo (`blind` hay `shown`).
- Dùng lại công tắc tenant `tenant_policy.auto_send` (đã có cho tin của chủ shop từ 2.9) và ngưỡng mặc định:
  - tối đa 3 tin bot liên tiếp trong một hội thoại khi khách chưa trả lời (chặn vòng lặp);
  - chỉ gửi trong giờ được cấu hình hay luôn gửi (mặc định: luôn gửi).

### 4.2. Trạng thái của một ý định

```
learning ──(chủ shop bật, đạt mức sẵn sàng)──▶ autopilot
autopilot ──(chủ shop tắt | tự hạ cấp | đổi cấu hình AI)──▶ learning
```

Mỗi lần đổi trạng thái ghi `learning_signals` (`autopilot_enabled` / `autopilot_demoted`, kèm lý do) và audit log. Agent vẫn ở `mode = learning`; Autopilot là lớp phủ theo ý định chứ không phải một mode thứ năm, để những tin bị chặn tự rơi về hành vi Learning.

## 5. Đường gửi

### 5.1. Luồng

```
tin khách ─▶ PolicyStage ─▶ AgentRunner (lưu nháp như bước 4)
                                 │
                                 ▼
                         AutopilotStage
                     ┌─ send_decision(nháp, trạng thái) ─┐
                 gửi được                            bị chặn
                     │                                  │
     outbound.send_ai_reply (khóa hội thoại,      nháp hiện cho nhân viên
     kiểm lại §3.4, ghi tin + outbox)             như Learning, ghi lý do
```

`AutopilotStage` chạy trong agent worker ngay sau runner, cùng `RunRequest`. Nó không gọi LLM.

### 5.2. Điều kiện của cổng

`send_decision` trả `None` (gửi) hoặc lý do đầu tiên bị chặn. Thứ tự từ rẻ tới đắt:

| Lý do chặn | Điều kiện |
|---|---|
| `kill_switch` | `ai_enabled = false` hoặc `auto_send = false` của tenant |
| `intent_not_enabled` | có ý định nào của lượt chưa ở trạng thái `autopilot` |
| `config_changed` | `config_key` của run khác khóa lúc bật (nối với §6.3) |
| `not_grounded` | `grounded = false`, hoặc `validate_draft` có vấn đề |
| `needs_human` | nháp đề xuất chuyển người |
| `handling` | hội thoại không ở `handling = bot`, hoặc đã đóng |
| `stale` | có tin khách mới hơn `based_on_sequence`, hoặc nhân viên đã trả lời |
| `bot_streak` | đã có 3 tin bot liên tiếp chưa được khách trả lời |
| `channel` | kênh không gửi được lúc này (§5.4) |
| `catalog_stale` | nháp có số tiền từ sản phẩm đồng bộ cũ hơn ngưỡng (A6) |

### 5.3. Ghi tin

- Migration nới `messages.sender_type` thêm `bot`, có review riêng. Thêm cột `draft_id` (nullable) vào `messages`.
- `outbound.send_ai_reply` giống `send_canned`: người gọi giữ `FOR UPDATE` trên hội thoại, ghi tin + outbox trong cùng transaction, idempotent theo `ai:<draft_id>`.
- Widget hiện tin bot với nhãn "Trợ lý tự động" (tenant đổi được chữ, không tắt được).
- Inbox hiện tin bot khác màu tin nhân viên; nhân viên vẫn chen vào bất cứ lúc nào, và tin nhân viên đầu tiên chuyển `handling` sang `human` như hiện nay.

### 5.4. Kênh

- **Webchat** (A3): gửi được ngay.
- **Messenger** (A6): chỉ trong cửa sổ 24 giờ kể từ tin khách cuối; bot luôn trả lời một tin khách vừa tới nên gần như luôn trong cửa sổ, nhưng vẫn kiểm.
- **Zalo OA** (A6): theo quy định gửi tin của Zalo OA cho tin trả lời; kiểm trong adapter kênh, không trong cổng.
- Kênh khác: `channel`, lùi về Learning.

## 6. Sau khi gửi: theo dõi và tự hạ cấp

Ở Autopilot không còn câu nhân viên để so, nên bước 3 không chấm được nữa. Thay vào đó đo bằng các tín hiệu sau:

### 6.1. Tín hiệu

- **Nhân viên đính chính:** nhân viên gửi tin trong 10 phút sau tin bot, trước khi khách trả lời. Ghi `ai_reply_followups` (có đổi số tiền / sản phẩm hay không, dùng lại `diff_features` của bước 4).
- **Khách phản ứng xấu:** lượt kế tiếp của khách có ý định `complaint` hay `human_request`, hoặc hỏi lại gần như nguyên văn.
- **Chủ shop chấm mẫu:** mỗi ngày lấy mẫu tối đa 10 tin bot mỗi ý định để chủ shop gán "đúng / gửi được nhưng nên sửa / sai". Dùng lại màn hình nhãn của bước 3.
- **Kiểm số tiền sau gửi:** chạy lại kiểm `amounts` với catalog và tri thức hiện tại; lệch là `conflict`.

### 6.2. Tự hạ cấp

Một ý định về lại Learning **ngay** khi:

- chủ shop gán nhãn "sai" cho một tin bot có số tiền hay sản phẩm sai, hoặc có `conflict` số tiền sau gửi;
- tỉ lệ nhân viên đính chính có đổi số tiền / sản phẩm > 2% trên 50 tin bot gần nhất;
- tỉ lệ khách phản ứng xấu > 5% trên 50 tin bot gần nhất.

Hạ cấp không xóa gì: các tin sau rơi về Learning, chủ shop thấy lý do và bật lại được khi số đo trở lại đạt.

### 6.3. Đổi cấu hình và tri thức

- Đổi template, phiên bản template, `PROMPT_VERSION`, route model ⇒ hạ cấp mọi ý định của agent (`config_changed`). Bước 3 bắt đầu đếm lại từ đầu với cấu hình mới.
- Đổi **tri thức** (bản phát hành mới) không hạ cấp, vì tri thức đổi hằng ngày nhờ Learning. Đổi lại, bản phát hành phải qua cổng eval có **bộ ca chuẩn theo ý định** cho mọi ý định đang Autopilot (A5). Đây là chỗ sửa giới hạn ghi ở thiết kế 05 §6.4: cổng hiện chỉ chạy ca bài học.
- Chủ shop sửa FAQ bằng tay cũng đi qua cổng này nếu tenant có ý định đang Autopilot; eval không đạt thì bản mới vẫn phát hành (chủ shop là nguồn sự thật) nhưng ý định liên quan bị hạ cấp kèm lý do `eval_regressed`.

## 7. Dữ liệu và API (dự kiến)

**Bảng mới**

- `autopilot_intents`: tenant_id, agent_id, intent, `state` (`autopilot` / `learning`), `config_key` jsonb, `source` (`blind` / `shown`), `enabled_by`, `enabled_at`, `demoted_at`, `demote_reason`. Khóa (tenant_id, agent_id, intent).
- `ai_reply_followups`: tenant_id, message_id (tin bot), kind (`staff_correction` / `customer_reaction` / `owner_label` / `amount_conflict`), chi tiết jsonb, created_at.
- `autopilot_decisions` không cần bảng riêng: lý do chặn ghi vào `agent_run_steps` (`Step("rule", "send_gate", ...)`) như các cổng khác.

**Cột mới**

- `messages.sender_type` thêm `bot`; `messages.draft_id`.
- `tenant_policy.max_bot_streak` (mặc định 3), giờ gửi tùy chọn.

**API**

- `GET /api/v1/autopilot/intents`: trạng thái từng ý định, kèm mức sẵn sàng và lý do hạ cấp gần nhất.
- `POST /api/v1/autopilot/intents/{intent}/enable`, `.../disable` (quyền `autopilot.manage`).
- `POST /api/v1/autopilot/pause` (tắt `auto_send` ngay).
- `GET /api/v1/autopilot/replies?intent=&days=`: tin bot đã gửi, tín hiệu theo dõi, để chủ shop chấm mẫu.

**Chỉ số**

- `atlas_autopilot_sent_total{intent}`, `atlas_autopilot_blocked_total{reason}`, `atlas_autopilot_demoted_total{reason}`.

## 8. Kiểm thử

- Hàm thuần `send_decision`: mỗi lý do chặn một test, test mặc định chặn khi thiếu dữ liệu, test lượt nhiều ý định.
- Tích hợp:
  - ý định đã bật, nháp hợp lệ ⇒ đúng một tin `bot` + outbox; chạy lại worker không gửi thêm;
  - khách nhắn thêm trong lúc runner chạy ⇒ `stale`, nháp hiện cho nhân viên;
  - nhân viên trả lời trước ⇒ không gửi;
  - nút dừng giữa chừng ⇒ không gửi;
  - đổi `PROMPT_VERSION` ⇒ ý định về Learning;
  - đính chính có đổi số tiền vượt ngưỡng ⇒ tự hạ cấp, có signal.
- Kiến trúc: `agent_runtime` không import `outbound`; chỉ `outbound` ghi `sender_type = 'bot'`.
- Eval red-team (thiết kế 02 §10) chạy với cổng thật: ca chèn lệnh "báo giá 1.000đ" phải bị chặn ở `not_grounded`.
- E2E: bật ý định `faq` trên webchat, khách hỏi giờ mở cửa, widget nhận tin có nhãn "Trợ lý tự động".

## 9. Trình tự

| Phần | Nội dung | Cần |
|---|---|---|
| A1 | Migration `sender_type = 'bot'`, `messages.draft_id`; `outbound.send_ai_reply`; hàm thuần `send_decision`; test kiến trúc. Chưa nối vào worker. | — |
| A2 | `autopilot_intents`, quyền `autopilot.manage`, API bật/tắt có kiểm mức sẵn sàng, nút trong màn hình "Chấm nháp", nút dừng. | A1 |
| A3 | `AutopilotStage` sau runner: kiểm lại dưới khóa, gửi webchat, lùi về Learning khi bị chặn, nhãn trong widget và inbox. | A2 |
| A4 | Theo dõi sau gửi (`ai_reply_followups`), chấm mẫu của chủ shop, tự hạ cấp, hạ cấp khi đổi cấu hình AI. | A3 |
| A5 | Bộ ca chuẩn theo ý định cho cổng eval; bản phát hành tri thức chạy chúng khi có ý định Autopilot; `eval_regressed`. | A3 |
| A6 | Messenger và Zalo OA; `price_quote` với kiểm độ mới catalog. | A4, A5 |

Mỗi phần một nhánh, nhiều commit nhỏ, merge commit.

## 10. Rủi ro và câu hỏi mở

- **Ngưỡng khởi điểm** (30 cặp, sai ≤ 2%, gửi được ≥ 85%) đặt cho shop nhỏ; shop nhiều tin có thể cần nhiều mẫu hơn. Giữ là cấu hình, xem lại sau tenant thật đầu tiên.
- **Mức sẵn sàng nguồn "đã thấy"** (Learning) dễ lạc quan hơn nguồn mù vì nhân viên có xu hướng gửi nguyên văn. Đề xuất: lần bật đầu tiên của một ý định cần nguồn mù; nguồn đã thấy chỉ dùng để bật lại sau hạ cấp.
- **Giờ gửi:** khách nhắn lúc 2 giờ sáng được bot trả lời ngay là lợi ích lớn nhất của Autopilot, nhưng cũng là lúc không ai đính chính được. Mặc định luôn gửi; có thể chặn `price_quote` ngoài giờ.
- **Gateway** chỉ chịu khoảng 2 request đồng thời: Autopilot không thêm lời gọi LLM, nhưng khách chờ trả lời nên ưu tiên đường soạn nháp hơn giám khảo và learning worker càng quan trọng.
- **Pháp lý và minh bạch:** nhãn "Trợ lý tự động" luôn hiện. Có cần hỏi ý khách trước không? Chưa; xem lại theo kênh.

## 11. Tóm tắt một dòng

Bot tự gửi chỉ cho những ý định đã đạt số đo, qua một cổng bằng code mặc định chặn; mọi thứ bị chặn rơi về Learning, và ý định tự mất quyền ngay khi có dấu hiệu sai.
