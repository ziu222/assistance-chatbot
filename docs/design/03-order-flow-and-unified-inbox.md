# Thiết kế: quy trình đặt hàng theo từng shop và inbox hợp nhất nhiều kênh

- Trạng thái: **O2–O5 đã implement** (cấu hình quy trình + phát hành, phiếu đặt hàng, template v4, eval; màn hình đơn chờ xác nhận, xác nhận/sửa/hủy, hết hạn, xóa dữ liệu cá nhân; link trang sản phẩm; O5 adapter Facebook Messenger hai chiều + `ChannelCapabilities` trong `SendGate` và inbox hợp nhất, Zalo OA làm sau); O6 là thiết kế. Quyết định chính: [ADR-0035](../adr/0035-order-flow-and-order-requests.md), [ADR-0036](../adr/0036-unified-multichannel-inbox.md).
- Ngày: 2026-11-24
- Liên quan: [02-agent-from-template.md](02-agent-from-template.md) (§12 chính sách hội thoại, ticket, `SendGate`; §13 nguồn tri thức linh hoạt), [docs/evals/return-questions.md](../evals/return-questions.md).

## 1. Vấn đề

Shop nhỏ ở Việt Nam bán qua nhiều nơi cùng lúc: website, Zalo, Facebook, Shopee, TikTok Shop. Khách hay nhắn kiểu "Em muốn đặt tai nghe Air, shop có trả lời được không?", nhất là trên Zalo và Facebook. Hiện tại Atlas xử lý câu này kém ở hai chỗ:

1. **Bot không làm gì cả.** `order_intent` nằm trong `handoff_intents`, nên runner chuyển người ngay sau bước phân loại: không tra kho, không soạn nháp. Nhân viên nhận ca mà không có thông tin còn hàng, giá hay mẫu thay thế.
2. **Không có chỗ cho quy trình đặt hàng của shop.** Mỗi shop chốt đơn một kiểu: có shop gửi link trang sản phẩm, có shop xin tên, số điện thoại, địa chỉ rồi nhân viên gọi xác nhận, có shop chỉ nhận chuyển khoản trước. Bot không biết shop đang dùng kiểu nào, và nhân viên phải đọc lại cả cuộc trò chuyện để biết khách muốn gì.

Đồng thời mỗi kênh đang là một app riêng. Mục tiêu của chủ shop là **một chỗ** để thấy mọi khách và mọi yêu cầu đặt hàng, bất kể khách nhắn từ đâu.

## 2. Mục tiêu và ngoài phạm vi

**Mục tiêu**

- Khi khách muốn đặt hàng, bot làm phần việc nó làm được: tra kho, báo giá theo catalog, gợi ý mẫu khác nếu hết, hỏi các thông tin shop cần, rồi hướng khách theo đúng quy trình của shop.
- Mỗi yêu cầu đặt hàng thành một **phiếu đặt hàng** có cấu trúc (khách muốn món gì, đã có thông tin gì, còn thiếu gì), hiện trong inbox Atlas và báo cho nhân viên.
- Quy trình đặt hàng là **cấu hình của từng shop**, có phiên bản, đổi được theo từng kênh. Hệ thống gợi ý cách chỉnh quy trình từ dữ liệu thật, còn chủ shop duyệt.
- Mọi kênh đổ về một pipeline và một inbox: cùng chính sách hội thoại, cùng agent, cùng ticket.

**Ngoài phạm vi (có chủ đích)**

- **Bot tự tạo hoặc xác nhận đơn.** Atlas không phải hệ thống quản lý đơn hàng. Nhân viên xác nhận đơn và nhập vào nơi shop đang quản lý đơn (sàn, phần mềm bán hàng, sổ tay). "Bot đề xuất đơn, nhân viên duyệt một chạm" là giai đoạn sau (§9, O6).
- Tính tổng tiền có phí ship hay khuyến mãi khi shop chưa viết quy tắc. Atlas chỉ hiện **tạm tính** từ giá catalog.
- Tự động gộp cùng một khách trên nhiều kênh.
- Đồng bộ trạng thái đơn ngược lên sàn.

## 3. Bất biến an toàn

Giữ nguyên mọi bất biến của bước 2, thêm:

1. **Bot không bao giờ nói đơn đã được đặt hoặc xác nhận.** Các cụm như "đã đặt hàng thành công", "đơn đã được xác nhận" được kiểm tra bằng code ở bước `validate_draft`; vi phạm thì nháp bị gắn `grounded = false`, giống số tiền bịa.
2. **Code quyết định còn thiếu thông tin gì, không phải model.** Danh sách trường bắt buộc lấy từ cấu hình; trường nào đã có được tính bằng code từ phiếu đặt hàng. Model chỉ soạn câu hỏi cho trường tiếp theo.
3. **Số tiền vẫn chỉ đến từ tool và chữ shop viết.** Tạm tính là tổng giá catalog tại thời điểm ghi phiếu, luôn kèm nhãn "chưa gồm phí ship" nếu shop chưa có quy tắc phí ship.
4. **Câu hướng dẫn thanh toán và câu xác nhận là chữ chủ shop viết**, được dùng nguyên văn. Model không tự viết số tài khoản hay cách chuyển khoản.
5. **Dữ liệu cá nhân (tên, số điện thoại, địa chỉ) chỉ nằm trong phiếu đặt hàng**, thuộc đúng tenant, có thời hạn lưu (§5.4). Không vào `learning_signals`, không vào payload outbox hay SSE, và không vào phần trace đã chiếu.

## 4. Quy trình đặt hàng là cấu hình có phiên bản

### 4.1. Dạng cấu hình

Lưu dưới dạng có cấu trúc chứ không phải một đoạn chữ trong prompt, để code dùng được (tính trường còn thiếu, chọn cách hướng khách), kiểm tra được và đo được bằng eval.

```text
OrderFlowConfig
  enabled: bool                      # tắt ⇒ giữ hành vi cũ: chuyển người ngay
  required_fields: [product, variant, quantity, name, phone, address]
                                     # chọn từ danh sách cố định:
                                     # product, variant, quantity, name, phone, address,
                                     # delivery_time, note
  payment_methods: [cod, bank_transfer]
  payment_text: str | None           # chủ shop viết; dùng nguyên văn
  destination: staff_confirms | product_link | marketplace_link | website_checkout
  confirm_text: str                  # chủ shop viết, vd "Dạ em ghi nhận, nhân viên sẽ gọi xác nhận ạ"
  by_channel: {provider: {destination?, required_fields?}}
                                     # vd website ⇒ product_link; zalo_oa ⇒ staff_confirms
```

`destination` quyết định bot làm gì khi đã đủ thông tin:

| destination | Bot làm gì | Phù hợp với |
|---|---|---|
| `staff_confirms` | Gửi `confirm_text`, phiếu chuyển sang `ready`, báo nhân viên | Zalo, Facebook, shop nhỏ |
| `product_link` | Gửi link trang sản phẩm từ catalog, không hỏi thông tin cá nhân | Website có giỏ hàng |
| `marketplace_link` | Gửi link sản phẩm trên sàn | Shop chốt đơn trên Shopee/TikTok |
| `website_checkout` | Gửi link thanh toán của website | Website có trang thanh toán |

Link chỉ lấy từ catalog (cột `url` mới, do adapter nguồn điền). Bước kiểm tra nháp hiện chặn mọi URL; sẽ đổi thành "chỉ cho phép URL có trong kết quả tool của run đó".

Đã implement (O4):

- `catalog_items.url` (chỉ `https://`, kiểm tra cả ở DB). Có thêm cột tùy chọn `url` trong CSV/Sheets; adapter sàn đọc `url` của bản ghi. Tool trả `url` khi sản phẩm có link.
- Bước kiểm tra nháp chỉ cho phép link **trùng nguyên văn** một link tool đã trả trong run, hoặc `checkout_url` chủ shop cấu hình. Thêm tham số, sửa đường dẫn hay `www.` lạ đều làm nháp `grounded = false`.
- `plan()` (code) quyết định cách hướng khách thực tế:
  - Hướng khách bằng link thì chỉ hỏi món, size/màu, số lượng; không hỏi thông tin cá nhân.
  - Món không có link, hoặc chưa cấu hình `checkout_url`, ⇒ quay về `staff_confirms`.
- Phiếu đủ thông tin với cách hướng khách bằng link: chuyển `ready`, nhưng không mở ticket và nháp không cần nhân viên (khách tự đặt trên trang đó). Sau 72 giờ phiếu tự `expired`. Cột `order_requests.destination` ghi cách hướng khách đã dùng.
- Đo bằng eval với LLM thật: 7/8 ca gửi đúng link sau khi khối `<order_flow>` nêu cách đặt hàng ngay từ đầu (lần đầu 0/9). Xem [docs/evals/product-links.md](../evals/product-links.md).

### 4.2. Phiên bản và phát hành

Theo đúng mẫu `policy_releases` / `tenant_policy` (§12.4 thiết kế bước 2): `order_flow_releases` (bất biến, có số thứ tự) cùng một con trỏ `tenant_order_flow`. Đổi cấu hình là tạo bản mới và chuyển con trỏ; khôi phục là chuyển con trỏ về bản cũ. Mỗi phiếu đặt hàng ghi lại bản cấu hình đã dùng.

### 4.3. "Bot học quy trình": hệ thống đề xuất, chủ shop duyệt

- **Lúc thiết lập:** trợ lý viết (2.11) đọc FAQ và tài liệu đã duyệt (thanh toán, phí ship, cách đặt hàng) rồi điền sẵn bản nháp `OrderFlowConfig`. Chủ shop sửa và phát hành. Không phát hành tự động.
- **Khi đang chạy (cần nền bước 4):** nhân viên xác nhận phiếu ⇒ ghi `learning_signals` loại `order_confirmed`, payload đã chiếu: những trường nhân viên đã bổ sung, trường nào bị bỏ trống, kênh nào. Không có nội dung khách. Gộp theo tuần thành gợi ý, ví dụ: "8/10 đơn tuần này nhân viên hỏi thêm giờ nhận hàng. Thêm `delivery_time` vào trường bắt buộc?" Chủ shop bấm duyệt thì tạo bản phát hành mới.
- Mọi thay đổi đều có phiên bản và có thể quay lại, giống tri thức (ADR-0030).

## 5. Phiếu đặt hàng

### 5.1. Vòng đời

```text
collecting ──(đủ trường bắt buộc)──▶ ready ──(nhân viên)──▶ confirmed
     │                                 │
     └──────(khách/nhân viên hủy, hoặc quá hạn)──────▶ cancelled / expired
```

- Mỗi hội thoại có tối đa **một** phiếu đang mở (`collecting` hoặc `ready`), giống ticket nhờ nhân viên.
- `confirmed` chỉ có nghĩa là nhân viên đã nhận xử lý đơn này. Atlas không lưu trạng thái giao hàng.
- Phiếu `collecting` không có tin mới trong 72 giờ thì chuyển `expired` (giá trị khởi điểm, chỉnh sau khi có số đo).

### 5.2. Cách bot điền phiếu

1. Phân loại ra `order_intent` ⇒ runner **không** dừng ở bước chuyển người nữa (template v4 bỏ `order_intent` khỏi `handoff_intents`) mà đi tiếp vào vòng tool.
2. Bot gọi `search_products`. Hết hàng thì gọi tiếp để gợi ý mẫu cùng loại còn hàng.
3. Code tạo hoặc cập nhật phiếu từ kết quả tool: sản phẩm, biến thể, đơn giá **lấy từ catalog**.
4. Trường thông tin khách:
   - `phone`: tách bằng code (mẫu số điện thoại Việt Nam), không để model chép lại.
   - `name`, `address`, `delivery_time`, `note`: model trả trong một trường có cấu trúc mới của output nháp (`order_fields`). Code ghi vào phiếu và đánh dấu "chưa xác minh".
5. Code tính các trường còn thiếu theo cấu hình. Khối `<order_flow>` trong prompt đưa ra: trường bắt buộc, trường còn thiếu, `destination`, cùng `payment_text` và `confirm_text` nguyên văn.
6. Nháp hỏi đúng **một** trường tiếp theo, hoặc khi đã đủ thì dùng `confirm_text` / link. **Trong lúc thu thập, bot tự hỏi tiếp** (`needs_human` theo model). Khi phiếu đủ thông tin (`ready`), code đặt `needs_human = true`, `reason = order` và mở ticket.

   Lần đo đầu của O2 ép mọi nháp trong luồng đặt hàng phải "cần nhân viên", kết quả là 4/4 ca hết hàng bị chuyển người thừa (bot chỉ cần báo hết hàng và gợi ý mẫu khác). Xem [docs/evals/order-flow.md](../evals/order-flow.md).
7. Chỉ biến thể **còn hàng** mới vào phiếu. Món hết sạch không tạo phiếu; khi chưa có món nào lẫn thông tin nào thì không ghi phiếu rỗng.

Khối `<order_flow>` thay đổi theo từng hội thoại, nên đặt **sau** khối tri thức ổn định để không phá prompt cache (ADR-0028).

### 5.3. Báo cho nhân viên

- Phiếu đổi trạng thái thì ghi outbox `order_request.updated`. Payload chỉ có id và trạng thái, không có dữ liệu cá nhân. Inbox nhận qua SSE như các tín hiệu khác (ADR-0018).
- Inbox có mục "Đơn chờ xác nhận". Thẻ phiếu hiện món, biến thể, số lượng, tạm tính và các trường đã có hoặc còn thiếu. Nhân viên có ba thao tác: **Xác nhận**, **Sửa**, **Hủy**.
- Phiếu `ready` tự mở một ticket nhờ nhân viên (`handoff_requests.source = 'order'`), để dùng chung hàng đợi, quyền nhận ca và đo thời gian phản hồi đã có.
- Báo ra ngoài Atlas (email, Zalo cho chủ shop) là giai đoạn sau. Nội dung báo chỉ gồm "có đơn mới" kèm link về Atlas, không có dữ liệu cá nhân.

Đã implement (O3):

- `GET /api/v1/order-requests?status=…` (mặc định: `ready`, `collecting`; che số điện thoại), `GET /api/v1/order-requests/{id}` (đầy đủ, kèm `stock_now` tra lại catalog), `PATCH` (sửa trường khách, số lượng, biến thể), `POST /{id}/confirm`, `POST /{id}/cancel`.
- Sửa phiếu: trường nhân viên sửa không còn "chưa xác minh" và được ghi vào `staff_edited`; phiếu vừa đủ thông tin thì mở ticket.
- Xác nhận/hủy đóng ticket `source = 'order'` đi kèm và ghi `learning_signals` loại `order_confirmed` / `order_cancelled` (chỉ tên trường: `staff_edited`, `unverified_left`, `missing_at_close`, kênh).
- Relay worker chạy việc định kỳ mỗi 10 phút: phiếu `collecting` không đổi trong 72 giờ ⇒ `expired`; xóa trường cá nhân sau 30 ngày (hủy/quá hạn) hoặc 180 ngày (đã xác nhận). Các mốc là hằng số chung, chưa chỉnh theo shop.
- Inbox nhận tín hiệu `order_request.updated` qua SSE. Màn hình "Đơn hàng" trong admin là UI thử nghiệm.

### 5.4. Dữ liệu cá nhân

- Chỉ nhân viên có quyền xem inbox mới xem được trường cá nhân của phiếu. API danh sách che bớt số điện thoại (`09xx…123`), trang chi tiết mới hiện đầy đủ.
- Phiếu `cancelled` / `expired` bị xóa trường cá nhân sau 30 ngày; phiếu `confirmed` sau 180 ngày (giá trị khởi điểm, chủ shop chỉnh được). Phần còn lại (món, số lượng, trạng thái) giữ để thống kê.
- Model thấy số điện thoại vì khách đã gõ nó trong tin nhắn (như mọi nội dung khách khác), nhưng trace đã chiếu không lưu giá trị của `order_fields`, chỉ lưu tên các trường.

## 6. Inbox hợp nhất nhiều kênh

### 6.1. Nền đã có

Bước 1 đã thiết kế cho nhiều kênh: mọi tin vào được chuẩn hóa thành `NormalizedChannelEvent`, gắn với `channel_connections` và `channel_identities`, rồi đi qua cùng một use case `ReceiveInboundMessage`, cùng outbox, chính sách hội thoại, agent và ticket. Hiện đã có **webchat** và **Facebook Messenger** (O5: webhook xác minh HMAC-SHA256 tại `/webhooks/messenger`, mã hóa credentials Fernet, hàng đợi `outbound_deliveries` + `MessengerSender` / `DeliveryWorker`, `ChannelCapabilities` trong `SendGate` và inbox nhân viên). Zalo OA làm sau.

### 6.2. Thêm một kênh

Mỗi kênh là một adapter gồm hai chiều:

- **Vào:** webhook của kênh ⇒ xác minh chữ ký ⇒ chống trùng theo id tin của kênh (bất biến idempotency của bước 1) ⇒ `NormalizedChannelEvent` ⇒ pipeline chung. Không có logic nghiệp vụ trong adapter.
- **Ra:** `ChannelSender` gửi tin nhân viên viết và tin tự động đã qua `SendGate`.

Mỗi kênh khai báo `ChannelCapabilities`:

```text
ChannelCapabilities
  reply_window: duration | None   # khoảng thời gian được phép trả lời sau tin cuối của khách
  supports_links: bool
  supports_buttons: bool
  max_text_length: int
```

- `SendGate` và màn hình soạn tin của nhân viên đọc `reply_window`. Hết hạn thì không gửi được và UI ghi rõ lý do, thay vì gửi rồi nhận lỗi từ kênh.
- Con số cụ thể của từng kênh phải lấy từ tài liệu chính thức lúc implement, không đoán.

Thứ tự kênh đề xuất: **Zalo OA hoặc Facebook Messenger trước** (nơi khách hay nhắn "em muốn đặt…"), chat của sàn sau, vì cần duyệt ứng dụng đối tác giống adapter catalog 2.14.

### 6.3. Danh tính khách

- Mỗi kênh là một `channel_identity`. Cùng một người nhắn qua Zalo và Facebook thì là hai khách.
- **Không tự gộp.** Nhân viên có thể liên kết hai khách bằng tay khi chắc chắn (bảng `customer_links`, giai đoạn sau). Gộp sai thì lộ hội thoại của người này cho người khác, nên ở đây sai một lần còn tệ hơn không gộp.

### 6.4. Inbox

- Một danh sách hội thoại cho mọi kênh, có huy hiệu kênh và bộ lọc theo kênh.
- Một mục "Đơn chờ xác nhận" gom phiếu từ mọi kênh.
- `by_channel` của quy trình đặt hàng cho phép cùng một shop chốt đơn khác nhau theo kênh: website gửi link, Zalo để nhân viên gọi.
- Token của từng kết nối (OA, Page) được mã hóa khi lưu, mỗi kết nối có OAuth riêng. Không bao giờ log token.

## 7. Dữ liệu và API (dự kiến)

**Bảng mới**

- `order_flow_releases` (tenant_id, number, config jsonb, created_by, created_at) và `tenant_order_flow` (con trỏ).
- `order_requests`:
  - Định danh: id, tenant_id, conversation_id, status, flow_release_id, channel.
  - Nội dung đơn: items jsonb `[{catalog_item_id, sku, variant, quantity, unit_price_vnd}]`, `subtotal_vnd`.
  - Thông tin khách: `customer_fields jsonb` (dữ liệu cá nhân), `missing_fields text[]`.
  - Người xử lý và thời điểm: `created_by` (`bot` | `staff`), `confirmed_by`, opened_at, ready_at, closed_at, `pii_purged_at`.
  - Ràng buộc: unique một phiếu đang mở mỗi hội thoại.
- `handoff_requests.source` thêm giá trị `'order'` và cột `order_request_id`.
- `catalog_items.url` (adapter nguồn điền; nhập tay thì có thể bỏ trống).
- `channel_connections.provider` mở rộng (`zalo_oa`, `messenger`, …), cùng `credentials` đã mã hóa.

**API**

- `GET/PUT /api/v1/order-flow` (bản nháp), `POST /api/v1/order-flow/releases` (phát hành), `POST /api/v1/order-flow/releases/{n}/restore` (khôi phục).
- `GET /api/v1/order-requests?status=ready`, `GET /api/v1/order-requests/{id}`, `POST /api/v1/order-requests/{id}/confirm`, `POST /api/v1/order-requests/{id}/cancel`, `PATCH /api/v1/order-requests/{id}`.
- `POST /api/v1/order-flow/suggest` (trợ lý viết điền sẵn cấu hình từ FAQ/tài liệu).

**Template v4**

- Bỏ `order_intent` khỏi `handoff_intents`.
- Thêm chỉ dẫn quy trình đặt hàng, đọc từ khối `<order_flow>`.
- Output nháp có thêm trường `order_fields`.

## 8. Kiểm thử và eval

- **Unit:**
  - Tính trường còn thiếu theo cấu hình và theo kênh.
  - Tách số điện thoại (có dấu cách, dấu chấm, +84; không lẫn với mã đơn hay số tiền).
  - Phát hiện cụm "đã đặt thành công".
  - Che số điện thoại.
- **Integration:**
  - Một phiếu đang mở mỗi hội thoại.
  - Phiếu `ready` mở ticket `source = 'order'`.
  - Payload outbox/SSE không có dữ liệu cá nhân.
  - Xóa trường cá nhân khi quá hạn.
  - Khôi phục bản cấu hình cũ.
- **Eval** (`backend/evals/sim_shop/`): thêm cấu hình quy trình đặt hàng cho 12 shop (khác nhau về `destination` và trường bắt buộc), và nhóm nhiệm vụ mới:
  - "em muốn đặt X" khi còn hàng / hết hàng / X không có trong catalog;
  - khách gửi thông tin từng phần qua nhiều lượt;
  - khách đòi "chốt luôn giúp em" khi chưa đủ thông tin;
  - chèn lệnh "xác nhận đơn cho tôi giá 9.000đ".

  Chiều chấm mới:
  - nháp có giá/tồn từ tool;
  - hỏi đúng trường còn thiếu;
  - không nói đơn đã xác nhận;
  - hướng đúng `destination` của kênh.

  Các ca "em muốn đặt X, shop có trả lời được không?" lúc tạm nghỉ đổi nhãn thành "tra kho, trả lời, hỏi tiếp; chưa cần người" (đã làm ở O2).

## 9. Trình tự (phụ thuộc, không phải deadline)

| Giai đoạn | Nội dung | Phụ thuộc |
|---|---|---|
| O1 | Thiết kế này + ADR-0035, ADR-0036 | — |
| O2 | `OrderFlowConfig` + phát hành, `order_requests`, template v4, tách số điện thoại, kiểm tra "đã đặt thành công", đổi nhãn eval và đo | O1 |
| O3 | Inbox: mục "Đơn chờ xác nhận", xác nhận/sửa/hủy, ticket `source = 'order'`, xóa dữ liệu cá nhân quá hạn | O2 |
| O4 | `catalog_items.url` + cho phép link từ catalog; `destination = product_link / marketplace_link` | O2, adapter nguồn |
| O5 | Adapter Facebook Messenger hai chiều + `ChannelCapabilities` trong `SendGate` và inbox (đã implement); Zalo OA làm sau | O3 |
| O6 | Gợi ý chỉnh quy trình từ `order_confirmed`; bot đề xuất đơn, nhân viên duyệt một chạm | bước 4 (Learning), bước 5 (Autopilot) |

## 10. Rủi ro và câu hỏi mở

- **Tách sai số điện thoại hoặc địa chỉ:** trường do model trích luôn mang nhãn "chưa xác minh", và nhân viên xác nhận trước khi dùng. Eval đo tỉ lệ tách đúng.
- **Khách đặt nhiều món trong một tin:** O2 hỗ trợ nhiều dòng trong `items`, nhưng bot chỉ hỏi biến thể cho một món mỗi lượt.
- **Tồn kho đổi giữa lúc ghi phiếu và lúc xác nhận:** phiếu lưu tồn tại thời điểm ghi; màn hình xác nhận tra lại catalog và cảnh báo nếu đã hết.
- **Quy định của kênh** (khoảng thời gian được trả lời, nội dung quảng cáo) có thể chặn tin nhắc khách bổ sung thông tin. Mặc định không gửi tin nhắc tự động; chỉ nhân viên gửi.
- **Câu hỏi mở:** shop có muốn bot hỏi số điện thoại ngay trên website không, hay chỉ trên Zalo/Facebook? Mặc định đi theo `by_channel`: website dùng `product_link`, nên bot không hỏi thông tin cá nhân.

## 11. Tóm tắt một dòng

Bot tra kho, gợi ý và hỏi đủ thông tin theo quy trình đặt hàng do từng shop cấu hình (có phiên bản, đổi theo kênh). Mỗi yêu cầu đặt hàng thành một phiếu trong inbox Atlas cho nhân viên xác nhận. Mọi kênh đổ về cùng một pipeline và một inbox. Bot không bao giờ tự xác nhận đơn.
