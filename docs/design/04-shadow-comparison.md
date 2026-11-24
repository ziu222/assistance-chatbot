# Thiết kế bước 3: so sánh nháp shadow với câu nhân viên thật sự gửi

- Trạng thái: **S1 và S2 đã implement** (ghép cặp, chấm bằng code; giám khảo, trần chi phí, lấy mẫu, bộ vàng); S3–S5 là thiết kế. Quyết định chính: [ADR-0037](../adr/0037-shadow-comparison-and-calibrated-judge.md).
- Ngày: 2026-11-24
- Liên quan: [02-agent-from-template.md](02-agent-from-template.md) (bất biến shadow, `agent_drafts`, §7.6 tri thức bền, §7.7 tầng "Học"), [03-order-flow-and-unified-inbox.md](03-order-flow-and-unified-inbox.md), [ADR-0030](../adr/0030-stateless-model-durable-knowledge.md), [docs/evals/](../evals/).

## 1. Vấn đề

Ở chế độ Shadow, agent soạn nháp cho mọi tin của khách nhưng không gửi gì. Nhân viên trả lời như bình thường và **không thấy nháp** (inbox chưa hiển thị nháp). Tới nay mọi con số chất lượng đều đến từ bộ shop giả lập (`backend/evals/sim_shop/`): đủ để so template và bắt lỗi, nhưng không trả lời được câu hỏi mà bước 5 (tự trả lời) cần:

> Với **shop này**, với **loại câu hỏi này**, nếu gửi nháp của bot thay cho câu nhân viên thì khách có nhận câu trả lời đúng không?

Mỗi lượt nhân viên trả lời một tin mà bot đã soạn nháp là một cặp so sánh có sẵn và miễn phí. Bước 3 biến các cặp đó thành số đo theo shop, theo ý định và theo phiên bản cấu hình AI.

## 2. Mục tiêu và ngoài phạm vi

**Mục tiêu**

- Ghép mỗi nháp với câu nhân viên gửi sau nó trong cùng hội thoại, theo quy tắc xác định.
- Chấm mỗi cặp **bằng code trước** (số tiền, sản phẩm, link, chuyển người), rồi mới bằng **LLM làm giám khảo**, có trần chi phí.
- **Không tin giám khảo khi chưa đo nó:** chủ shop gán nhãn một mẫu nhỏ, hệ thống đo mức đồng thuận giữa giám khảo và người, rồi mới dùng điểm của giám khảo để đề xuất bật tự trả lời.
- Tổng hợp theo tenant × ý định × phiên bản (template, `PROMPT_VERSION`, model, knowledge hash): tỉ lệ nháp gửi được, tỉ lệ nháp sai, chuyển người đúng/thừa/thiếu, chỗ thiếu tri thức. Từ đó ra **mức sẵn sàng** cho từng ý định.
- Giữ nguyên bất biến của bước 2: không có gì đến tay khách; dữ liệu chấm điểm không chép nội dung khách vào tín hiệu học.

**Ngoài phạm vi (có chủ đích)**

- Hiển thị nháp cho nhân viên, nhân viên sửa nháp, ứng viên tri thức (bước 4).
- Tự gửi nháp (bước 5). Bước 3 chỉ **đề xuất** ý định nào đủ điều kiện; chủ shop quyết.
- Dùng câu nhân viên làm "đáp án đúng tuyệt đối". Nhân viên cũng có thể sai hoặc lỗi thời (§4.3).

## 3. Bất biến

1. **Chỉ đọc đường trả lời.** Bước 3 không đổi nháp, không đổi hội thoại, không gửi tin, không ảnh hưởng run đang chạy. Lỗi ở bước 3 chỉ làm thiếu số đo.
2. **Mỗi cặp ghi rõ nhân viên có thấy nháp không** (`draft_exposure`). Ở bước 3 luôn là `hidden`. Khi bước 4 hiển thị nháp, nhân viên có thể chép nháp; so sánh lúc đó bị lệch nên phải tính riêng, không gộp với số đo "mù".
3. **Code trước, giám khảo sau.** Mâu thuẫn về số tiền hay sản phẩm do code bắt thì là kết luận, không chờ LLM.
4. **Giám khảo không tự phong.** Điểm của giám khảo chỉ dùng cho đề xuất bật tự trả lời khi đã có đo đồng thuận với nhãn người (§6).
5. **Tín hiệu học chỉ có id, nhãn và tên trường**, như các tín hiệu khác. Lời giải thích của giám khảo không được trích dữ liệu cá nhân (số điện thoại, địa chỉ) và có thời hạn lưu.

## 4. Ghép cặp

### 4.1. Quy tắc

Với mỗi nháp `d` (dựa trên tin tới `based_on_sequence = S`):

- **Câu tham chiếu** là các tin `sender_type = 'human'` có `sequence > S`, tính từ tin nhân viên đầu tiên sau `S` cho tới tin khách kế tiếp, hoặc tối đa 10 phút sau tin nhân viên đầu tiên. Các tin đó ghép thành một câu (nhân viên hay gửi thành nhiều tin ngắn).
- Tin `system` (tin tự động của `SendGate`) không phải câu tham chiếu.
- **Chỉ nháp mới nhất trước câu tham chiếu được chấm.** Các nháp cũ hơn trong cùng cụm (khách nhắn thêm trước khi nhân viên trả lời) được đánh dấu `superseded`.
- Nhân viên chưa trả lời sau 24 giờ, hoặc khách nhắn tiếp mà không ai trả lời ⇒ `unanswered`. Không chấm, nhưng vẫn đếm: đây là chỗ bot có giá trị nhất khi tự trả lời.
- Run `skipped` / `failed` không có nháp ⇒ không có cặp. Tỉ lệ này đã có ở `agent_runs`.

### 4.2. Thời điểm

Một việc định kỳ trong worker (như việc hết hạn phiếu đặt hàng ở thiết kế 03) chạy mỗi phút. Nó tìm nháp đã có câu tham chiếu **ổn định**, tức tin nhân viên cuối đã cũ hơn 2 phút hoặc đã có tin khách mới, mà chưa có so sánh. Khóa bằng `FOR UPDATE SKIP LOCKED` như lịch sync Sheets. Có lệnh chạy lại cho dữ liệu cũ (backfill).

### 4.3. Câu nhân viên không phải đáp án tuyệt đối

Nhân viên có thể báo giá cũ, quên chính sách, hoặc trả lời cụt. Vì vậy:

- Code so số tiền của cả hai với **catalog và tri thức tại thời điểm soạn nháp** (`knowledge_hash` của run). Nhân viên nêu giá không có trong catalog ⇒ cặp được gắn `reference_suspect`, không tính bot sai.
- Giám khảo có nhãn riêng `reference_questionable`. Các cặp này vào hàng xem lại của chủ shop, không vào điểm.

## 5. Chấm bằng code (miễn phí, mọi cặp)

| Kiểm tra | Cách làm | Kết quả |
|---|---|---|
| Số tiền | `extract_amounts` trên nháp và câu tham chiếu, so với giá hợp lệ của run | `agree` / `conflict` / `draft_missing` / `draft_extra` |
| Sản phẩm | SKU/id trong nguồn của nháp so với sản phẩm nhắc trong câu tham chiếu (tên hoặc SKU, so không dấu) | khớp / khác / không xác định |
| Chuyển người | nháp `needs_human` so với hành vi nhân viên: trả lời ngay một câu có thông tin, hay chỉ nói "để em kiểm tra" rồi hỏi người khác hoặc mở ticket | đúng / thừa / thiếu (ước lượng) |
| Link | link trong nháp so với link trong câu tham chiếu | khớp / khác |
| Thời gian | lúc nháp sẵn sàng so với lúc nhân viên trả lời | bot nhanh hơn bao nhiêu giây |
| Nháp đã bị Atlas chặn | `grounded = false` | `not_sendable` ngay, không cần giám khảo |

`conflict` về số tiền hoặc sản phẩm (mà câu tham chiếu không bị nghi) ⇒ cặp có kết luận `wrong` từ code, không cần giám khảo.

Đã implement (S1):

- Module `shadow`:
  - `domain/pairing.py` (ghép cặp) và `domain/checks.py` (chấm bằng code) là hàm thuần, có test architecture.
  - `infrastructure/comparisons.py` là việc định kỳ mỗi phút trong relay worker.
- Bảng `shadow_comparisons` (migration 0018). Mỗi nháp có tối đa một dòng (unique `draft_id`); câu tham chiếu chỉ lưu số thứ tự tin, không chép nội dung.
- Nháp `pending` không tạo dòng và được xem lại ở lượt sau. Lượt đầu tự xử lý mọi nháp cũ (backfill). Duyệt theo trang (keyset), khóa nháp bằng `SKIP LOCKED`.
- `known_amounts` gồm mọi giá catalog và số tiền trong FAQ/tài liệu **hiện tại** (chưa có ảnh chụp tri thức đúng lúc soạn nháp); câu nhân viên vì thế có thể bị nghi nhầm khi giá vừa đổi.
- Metric: `shadow_comparisons_total{status, verdict}`, `shadow_pairing_lag_seconds`.
- Chưa có API hay màn hình (S4). Chạy trên DB dev: 8 nháp cũ đều `unanswered` (hội thoại thử chưa ai trả lời).

## 6. LLM làm giám khảo (có trần chi phí, có hiệu chỉnh)

### 6.1. Đầu vào và đầu ra

Giám khảo nhận: các tin khách của cụm, nháp, câu tham chiếu, và kết quả chấm bằng code. Nó không nhận tên shop, tên khách hay lịch sử khác. Output có cấu trúc:

```text
JudgeOut
  verdict: equivalent | acceptable_different | worse | wrong | reference_questionable
  sendable: as_is | minor_edit | rewrite | must_not_send
  missing_info: bool       # câu tham chiếu có thông tin quan trọng mà nháp thiếu
  contradicts: bool        # nháp nói ngược câu tham chiếu ở một điểm quan trọng
  tone_ok: bool
  rationale: str (≤ 300 ký tự, không trích dữ liệu cá nhân)
```

- **"Gửi được"** = `as_is` hoặc `minor_edit`. **"Sai"** = `wrong`, hoặc `must_not_send`, hoặc `contradicts`.
- Hướng dẫn chấm nêu rõ: khác câu chữ nhưng cùng thông tin là `equivalent`; bot thận trọng chuyển người khi nhân viên tự trả lời được là `worse`, không phải `wrong`.

### 6.2. Model và chi phí

- **Route mới `judge`** trong `routes.toml`, dùng model **khác** model soạn nháp, để giảm thiên vị tự chấm. Mặc định `claude-sonnet-5-5`, OpenAI là dự phòng (đang tạm khóa).
- Trần chi phí riêng mỗi tenant mỗi ngày (`usage_events.purpose = 'judge'`), như trợ lý viết.
- **Lấy mẫu:** chấm 100% cặp cho tới N cặp/ngày/tenant, sau đó lấy mẫu theo ý định để ý định hiếm vẫn đủ mẫu.
- Chạy nền ưu tiên thấp, mỗi lần 1 lời gọi: gateway hiện chịu khoảng 2 request đồng thời, và đường soạn nháp phải được ưu tiên.

### 6.3. Hiệu chỉnh với nhãn người

- **Bộ vàng cho giám khảo** (`backend/evals/judge/`): khoảng 40 cặp viết tay gồm khách, nháp, câu tham chiếu và nhãn đúng, phủ các trường hợp khó: khác chữ cùng ý, thiếu một chi tiết, sai giá, chuyển người thừa, nhân viên sai. Chạy trong eval như mọi bộ khác; đổi prompt hay model giám khảo phải qua bộ này.
- **Nhãn của chủ shop:** màn hình liệt kê một mẫu cặp đã chấm. Chủ shop bấm "gửi được / phải sửa / sai" cho mỗi cặp, mất vài giây một cặp. Từ đó đo mức đồng thuận (Cohen's kappa) giữa giám khảo và chủ shop **theo từng tenant**.
- Kappa dưới 0,6 hoặc dưới 30 nhãn ⇒ màn hình hiển thị điểm giám khảo kèm cảnh báo "chưa hiệu chỉnh", và **không** đưa ra đề xuất bật tự trả lời.

Đã implement (S2):

- `shadow/domain/judge.py` gồm `JudgeOut`, prompt (`JUDGE_PROMPT_VERSION`) và `finalize`: code kết luận được thì thắng; `contradicts` ⇒ `wrong`.
- `shadow/infrastructure/judging.py` (`JudgeWorker`) chạy trong agent worker:
  - Chấm từng cặp một, nghỉ 2 s giữa các lần.
  - Có trần chi phí riêng `JUDGE_DAILY_CAP_MICRO` (mặc định 0,2 USD/tenant/ngày) và lấy mẫu (`JUDGE_FULL_PER_DAY`, `JUDGE_PER_INTENT_AFTER`).
  - Lỗi provider ⇒ thử lại tối đa 3 lần rồi `gave_up`.
- `spent_today_micro` nhận `purpose`; trần chi phí của việc soạn nháp không còn tính tiền của trợ lý viết lẫn giám khảo.
- Route `judge` dùng `claude-sonnet-5-5` (đã thêm giá, `PRICE_VERSION` 2026-11-24).
- Migration 0019 thêm các cột của giám khảo và kết luận cuối (`final_verdict`, `sendable`).
- Bộ vàng 32 ca: khớp gửi được/không **31/32**, khoảng 0,09 USD mỗi lần chạy. Xem [docs/evals/judge.md](../evals/judge.md). Bộ vàng do người thiết kế gán nhãn, nên nhãn của chủ shop ở S3 vẫn bắt buộc.

## 7. Tổng hợp và mức sẵn sàng

Mỗi tenant × ý định × phiên bản cấu hình AI (template version, `PROMPT_VERSION`, model phục vụ, `knowledge_hash`):

- số cặp, tỉ lệ `unanswered`;
- tỉ lệ gửi được, tỉ lệ sai (code + giám khảo), tỉ lệ chuyển người đúng/thừa/thiếu;
- bot nhanh hơn nhân viên bao nhiêu (trung vị);
- chủ đề thiếu nguồn hay gặp (nối với `knowledge_gap` của 2.13).

**Đề xuất bật tự trả lời cho một ý định** (bước 5 dùng; giá trị khởi điểm, chủ shop chỉnh):

- ít nhất 30 cặp được chấm trong 14 ngày gần nhất, với cùng phiên bản cấu hình;
- tỉ lệ sai ≤ 2% và tỉ lệ gửi được ≥ 85%;
- giám khảo đã hiệu chỉnh cho tenant (§6.3);
- không có cặp `conflict` số tiền nào trong 7 ngày.

Đổi phiên bản cấu hình (template, prompt, model, knowledge release) thì bộ đếm của phiên bản mới bắt đầu lại từ đầu, vì số đo của phiên bản cũ không nói gì về phiên bản mới.

## 8. Dữ liệu và API (dự kiến)

**Bảng mới**

- `shadow_comparisons`:
  - Định danh: id, tenant_id, conversation_id, draft_id (unique), run_id.
  - Câu tham chiếu: `reference_from_seq`, `reference_to_seq` (id tin, không chép nội dung).
  - Trạng thái: `status` (`scored` / `superseded` / `unanswered` / `skipped`), `draft_exposure` (`hidden` / `shown`), `intent`, `config_version` (jsonb: template, prompt, model, knowledge_hash).
  - Kết quả: `code_checks` (jsonb), `code_verdict`, `judge` (jsonb `JudgeOut`, nullable), `judge_model`, `judge_cost_micro`, `final_verdict`, `sendable`.
  - Thời gian: `bot_lead_seconds`, created_at, `rationale_purged_at`.
- `judge_labels`: comparison_id, nhãn của chủ shop, người gán, thời điểm.

**API**

- `GET /api/v1/shadow/summary?days=14` (theo ý định, kèm mức sẵn sàng).
- `GET /api/v1/shadow/comparisons?intent=&verdict=` (danh sách cặp; nội dung đọc từ `messages` / `agent_drafts` lúc hiển thị).
- `POST /api/v1/shadow/comparisons/{id}/label`.
- `GET /api/v1/shadow/calibration` (kappa, số nhãn).

**Tín hiệu học:** `shadow_compared` với payload `{comparison_id, intent, final_verdict, sendable, code_flags}`, nguồn cho bước 4 (ý định nào hay sai thì ưu tiên học ở đó).

**Metric:** `shadow_comparisons_total{verdict, intent}`, `shadow_judge_cost_micro_total`, `shadow_pairing_lag_seconds`.

## 9. Kiểm thử

- **Unit:**
  - quy tắc ghép cặp (nhiều tin nhân viên, khách nhắn chen, tin `system`, quá 24 giờ, nhiều nháp trong một cụm);
  - chấm bằng code (số tiền trùng/khác/thiếu, sản phẩm theo tên không dấu, giá nhân viên bị nghi);
  - công thức kappa;
  - điều kiện sẵn sàng (biên 30 cặp, 2%, 85%).
- **Integration:**
  - việc định kỳ ghép đúng và không chấm trùng khi chạy song song;
  - backfill;
  - giám khảo qua `FakeLLM`;
  - trần chi phí giám khảo;
  - tenant isolation;
  - tín hiệu học không có nội dung khách.
- **Eval:**
  - bộ vàng của giám khảo (§6.3), báo cáo độ chính xác theo nhãn;
  - chạy lại khi đổi model hay prompt của giám khảo.
- **Mô phỏng đầu cuối:** dùng bộ shop giả lập để sinh "câu nhân viên" từ đáp án của nhiệm vụ, đẩy qua toàn bộ đường ghép cặp và chấm. Kiểm tra số tổng hợp khớp với điểm eval đã biết.

## 10. Trình tự

| Giai đoạn | Nội dung | Phụ thuộc |
|---|---|---|
| S1 | `shadow_comparisons`, ghép cặp, chấm bằng code, việc định kỳ + backfill, metric | — |
| S2 | Route `judge`, `JudgeOut`, trần chi phí + lấy mẫu, bộ vàng của giám khảo trong eval | S1 |
| S3 | Màn hình gán nhãn (UI thử nghiệm), `judge_labels`, kappa theo tenant | S2 |
| S4 | Tổng hợp theo ý định × phiên bản, mức sẵn sàng, báo cáo tuần | S3 |
| S5 | Tín hiệu `shadow_compared` cho bước 4; tách số đo `hidden` / `shown` khi bước 4 hiển thị nháp | S4, bước 4 |

## 11. Rủi ro và câu hỏi mở

- **Ít cặp:** shop nhỏ có thể mất nhiều tuần mới đủ 30 cặp cho một ý định. Báo rõ "chưa đủ mẫu" thay vì đưa con số trên 5 cặp. Ý định hiếm có thể không bao giờ đủ, và đó là chấp nhận được.
- **Nhân viên trả lời qua kênh khác** (gọi điện, Zalo cá nhân) ⇒ hội thoại có vẻ `unanswered`. Đếm riêng, không coi là bot đúng hay sai.
- **Giám khảo thiên vị câu dài hoặc giọng giống nó.** Đã giảm bằng model khác model soạn nháp, bộ vàng có ca "ngắn mà đúng", và kappa với chủ shop.
- **Chi phí:** mỗi lần chấm khoảng một lời gọi Sonnet với prompt ngắn (không có khối tri thức). Có trần ngày và lấy mẫu. Ước tính chính xác sau S2.
- **Quyền riêng tư:** giám khảo thấy nội dung khách như lúc soạn nháp (ADR-0027). Lời giải thích bị xóa sau 90 ngày; nhãn và kết luận giữ lại.
- **Câu hỏi mở:** chủ shop có muốn nhân viên (không chỉ chủ shop) gán nhãn không? Mặc định chỉ `owner` / `admin`, vì nhãn quyết định việc bật tự trả lời.

## 12. Tóm tắt một dòng

Mỗi lần nhân viên trả lời một tin mà bot đã soạn nháp "mù", hệ thống ghép cặp, chấm bằng code rồi bằng giám khảo đã được hiệu chỉnh với nhãn của chủ shop, và tổng hợp theo ý định để biết chỗ nào bot đủ tin cậy cho bước 5. Không có gì đến tay khách.
