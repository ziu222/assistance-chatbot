# Lý do chuyển người: định nghĩa lại và đo lại

Lần so sánh template v1/v2 ([agent-eval-template-v2.md](agent-eval-template-v2.md)) có chiều "khớp lý
do chuyển người" chỉ đạt 33% (3/9), dù mọi ca đó đều chuyển người đúng. Ba nguyên nhân:

1. **Model không được biết lý do nghĩa là gì.** Prompt chỉ có tên các giá trị (`no_source`, `policy`,
   `order`, `discount`, `complaint`, `unclear`) trong schema, không có định nghĩa hay thứ tự ưu tiên khi
   một ca khớp nhiều lý do.
2. **Nhãn của bộ dữ liệu tự mâu thuẫn.** Cùng là "đơn chưa giao", ca bực bội gán `complaint` còn ca
   hỏi trạng thái đơn gán `order`. "Xin đổi màu" khi khách bực gán `complaint`, còn khi khách hỏi bình
   thường thì lại là `policy` hoặc `no_source`, tùy shop có chính sách đổi trả hay không.
3. **Ca xin ngoại lệ luôn gán `policy`**, kể cả ở shop chưa viết chính sách đổi trả. Ở những shop đó
   bot đúng khi trả `no_source`.

## Sửa

- `DRAFT` (`PROMPT_VERSION` 2026-11-24.1) định nghĩa từng lý do, kèm thứ tự ưu tiên (lấy mục đầu tiên
  đúng): `no_source` → `order` → `discount` → `policy` → `complaint` → `unclear`. `no_source` đứng đầu
  vì đây là lý do duy nhất sinh tín hiệu `knowledge_gap`.
- Bộ dữ liệu (sinh lại với seed 42), theo cùng quy tắc:
  - đơn chưa giao ⇒ `order`;
  - xin đổi hàng hoặc xin ngoại lệ ⇒ `policy` nếu shop có chính sách đổi trả, không thì `no_source`.
- Chấm điểm `must_not_say` khớp nguyên cụm. Trước đây giá ảo "9.000đ" bị coi là đã nói khi nháp báo
  giá thật "179.000đ" hay "309.000đ". Lỗi này làm chiều "không nói điều cấm" tụt xuống 96% ở lần đo
  đầu tiên sau khi sửa prompt.

## Kết quả (76 nhiệm vụ, template v2, 2026-11-23 20:10)

| Chỉ số | Trước (v2, 14:00) | Sau |
|---|---|---|
| Khớp lý do chuyển người | 33% (3/9) | **100% (7/7)** |
| Đúng việc có/không chuyển người | 92% (70/76) | 99% (75/76) |
| Gọi đúng tool | 94% (17/18) | 100% (18/18) |
| Trích đúng nguồn sản phẩm | 84% (21/25) | 96% (24/25) |
| Không nói điều cấm | 100% (46/46) | 100% (49/49) |
| Ca thiếu căn cứ: chuyển người, không bịa | 100% (7/7) | 100% (10/10) |
| Chi phí | 0,38 USD | 0,29 USD |
| Độ trễ p50 / p95 mỗi nhiệm vụ | 2,5 s / 8,9 s | 3,2 s / 12,2 s |

Mẫu nhỏ (7–9 ca cho chiều lý do), nên chỉ đọc đây là hết lỗi hệ thống, chưa phải con số chính xác.
Số ca không chuyển người đúng nhiều hơn một phần vì nhãn đã sửa (ca `no_source` tăng từ 7 lên 10).

**Còn lại:**

- `beauty_gl-faq-012`: khách hỏi chính sách đổi trả (câu hỏi thông tin, FAQ có câu trả lời). Bot
  trả lời đúng nhưng vẫn đặt `needs_human=true`. Nguyên nhân là chỉ dẫn "đổi/trả luôn cần nhân viên"
  của v2 áp cả cho câu hỏi về chính sách, không chỉ cho yêu cầu đổi hàng.
- Độ trễ Claude với key chính thức vẫn **chưa đo được**. `LLM_BASE_URL` trong `.env` đang trỏ tới
  gateway thử nghiệm. Lần này Claude chỉ phục vụ 9 lời gọi, còn lại do OpenAI dự phòng; p95 12 s là
  của hỗn hợp đó. Muốn đo thì để trống `LLM_BASE_URL` và đặt key Anthropic chính thức vào
  `CLAUDE_API_KEY`.

Chạy lại: `uv run python backend/evals/run_eval.py --template-version 2 --per-category 4`.
