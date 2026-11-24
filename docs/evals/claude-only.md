# Chỉ dùng Claude qua gateway (tắt OpenAI dự phòng)

Ngày 2026-11-24. Credit OpenAI sắp hết, nên chuyển sang chỉ dùng Claude (`LLM_DISABLED_PROVIDERS=openai`).
Key Claude chỉ dùng được qua UniversalAI Gateway (`LLM_BASE_URL`); gọi thẳng API chính thức của
Anthropic bằng key đó thì nhận 401.

## Trước khi sửa: 38/40 run thất bại

Lần chạy đầu với OpenAI bị tắt: 40 nhiệm vụ, 2 ca mỗi nhóm, 4 ca chạy song song. Chỉ 8 lời gọi
Claude thành công, 38 run thất bại. Có hai nguyên nhân, đều bị che đi lâu nay vì OpenAI nhận thay
các lời gọi lỗi:

1. **Gateway trả 429 khi nhận nhiều request cùng lúc.**
   - Từng lời gọi đơn mất khoảng 2 giây; structured output, tool và 12 lời gọi nhỏ song song đều
     chạy được.
   - Eval chạy 4 ca song song, mỗi ca 2–6 lời gọi với prompt dài, thì nhận 429.
   - FailoverLLM coi 429 là provider có vấn đề. Sau 3 lần liên tiếp, circuit breaker mở 30 giây và
     mọi run sau đó thất bại ngay (khoảng 200 ms).
2. **Claude gọi tool từng cái một**, ví dụ tra kho, rồi xem chi tiết, rồi tra tiếp. Câu hỏi sản
   phẩm vì vậy cần phân loại cộng 4 lượt soạn, vượt `max_model_calls = 4`. Chạy tuần tự 20 ca thì
   4 run dừng ở `budget_exceeded`.

## Sửa

- **429 ⇒ chờ rồi thử lại cùng đích**, tối đa 2 lần: chờ theo `Retry-After` nếu có, không thì lùi
  1,5 s rồi 4 s, có jitter. Toàn bộ nằm trong trần timeout của đích. Hết lượt thử mới tính là lỗi
  và mới ảnh hưởng tới circuit breaker.
- **Template v5:** `max_model_calls` 4 → 6, `max_tool_calls` 5 → 6. Trần chi phí mỗi run giữ 0,05
  USD. Agent tạo từ bản cũ giữ ảnh chụp cũ.

## Sau khi sửa: 80 nhiệm vụ, 4 ca mỗi nhóm, 4 ca song song, chỉ Claude Sonnet 5

| Chỉ số | Chủ yếu OpenAI (v4, lần đo trước) | Chỉ Claude (v5) |
|---|---|---|
| Run thất bại | 0 | **0** (178/178 lời gọi do Claude phục vụ) |
| Đúng việc có/không chuyển người | 80/80 | 76/80 |
| Gọi đúng tool | 23/24 | **24/24** |
| Khớp lý do chuyển người | 9/10 | **13/13** |
| Không nói điều cấm | 57/57 | 61/61 |
| Nháp qua kiểm tra nguồn/giá/link | 57/57 | 60/61 |
| Ca thiếu căn cứ: chuyển người, không bịa | 9/9 | 9/9 |
| Chi phí / 80 nhiệm vụ | 0,47 USD | 0,84 USD |
| Độ trễ mỗi nhiệm vụ p50 / p95 | 3,3 s / 18 s | **21 s / 37 s** |

**Đọc số:**

- **4 ca chuyển người thừa:** khách hỏi kèm một ý shop chưa có thông tin (giờ trực, giá nhập,
  so sánh chi tiết). Claude trả lời phần có nguồn rồi nhờ nhân viên phần còn lại (`no_source`), tức
  thận trọng hơn chứ không bịa.
- **1 nháp bị chặn:** bot tự tính tổng "3 sản phẩm là 540.000đ"; bước kiểm tra chỉ nhận giá niêm yết.
  Phiếu đặt hàng đã có tạm tính do code tính, nên có thể cho phép đúng số đó trong nháp.
- **Ca link còn thiếu:** bot hỏi lại màu vì khách chưa nêu, nên nhãn của ca này là quá khắt khe.
- **Độ trễ là vấn đề chính.** p50 21 s khi 4 ca chạy song song là do thời gian chờ 429. Chạy tuần
  tự, p50 khoảng 6,8 s và p95 khoảng 12,7 s (20 ca). Lưu lượng thật của một shop thấp hơn eval nhiều,
  nhưng worker agent mặc định chạy 4 run song song, nên giờ cao điểm có thể gặp cùng tình huống.
- **Chi phí** gần gấp đôi lần đo chủ yếu OpenAI, vì Claude dùng nhiều lượt hơn (178 lời gọi so với
  khoảng 170 lời gọi của hai model OpenAI, và prompt mỗi lượt lớn hơn).

## Việc nên làm tiếp

- Đo giới hạn tốc độ thật của gateway (request hay token mỗi phút) rồi đặt
  `AGENT_WORKER_CONCURRENCY` cho khớp, thay vì để 429 điều tiết.
- Cho phép tạm tính của phiếu đặt hàng làm số tiền hợp lệ trong nháp.
- Bật lại OpenAI dự phòng khi có credit: xóa dòng `LLM_DISABLED_PROVIDERS=openai` trong `backend/.env`.

Chạy lại: `uv run python backend/evals/run_eval.py --template-version 5 --per-category 4`.
