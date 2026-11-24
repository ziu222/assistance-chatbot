# Giám khảo so sánh shadow: bộ vàng (thiết kế 04, giai đoạn S2)

Bộ vàng gồm 32 cặp viết tay trong `backend/evals/judge/golden.json`, mỗi cặp gồm tin khách, nháp, câu nhân viên và nhãn người. Nhãn `sendable` có hai giá trị: *good* (gửi được như hoặc sửa nhẹ) và *bad* (phải viết lại hoặc không được gửi). Bộ phủ các trường hợp khó: khác chữ cùng ý; khác cách nhưng vẫn đúng; thiếu chi tiết; chuyển người thừa; sai tồn kho, chính sách hay lời hứa; câu nhân viên cụt hoặc lạc đề; giọng cộc; và thiên vị câu dài. Route `judge` dùng `claude-sonnet-5-5`, khác model soạn nháp (`claude-sonnet-5`). Ngày chạy: 2026-11-24.

| Prompt | Khớp gửi được | Verdict chấp nhận được | Chi phí / 32 ca | p50 |
|---|---|---|---|---|
| 2026-11-24.1 | 27/32 | 28/32 | 0,09 USD | 4,6 s |
| **2026-11-24.2** | **31/32** | 31/32 | 0,09 USD | 4,2 s |

**Lần 1:** giám khảo coi mọi chi tiết mà câu nhân viên không nhắc tới (giá, độ pH, giờ đóng cửa) là chưa xác minh, nên chấm nháp đúng là sai hoặc phải viết lại. Lỗi này nặng nhất ở các ca câu nhân viên cụt (1/4).

**Sửa (lần 2):** prompt nói rõ nháp đến tay giám khảo đã qua kiểm tra của Atlas (giá khớp catalog/tri thức, sản phẩm lấy từ tra cứu); chỉ coi là sai khi nháp mâu thuẫn với câu nhân viên hoặc hứa điều nhân viên không hứa. Điều này đúng với pipeline thật: nháp có `grounded = false` bị code kết luận `not_sendable` và không bao giờ đến giám khảo.

**Còn lệch:**

- `ad-01`: nháp báo hết size và gợi ý size khác, còn nhân viên báo khoảng một tuần nữa có hàng lại. Giám khảo chấm *worse / rewrite*. Có thể chính giám khảo đúng, nhãn của ca này còn bàn được.
- `tn-02`: giám khảo chấm *wrong* thay vì *worse*. Kết luận gửi được hay không vẫn đúng (không được gửi).

**Giới hạn:** bộ vàng do người viết thiết kế tự gán nhãn, nên prompt có thể bị chỉnh vừa với chính những nhãn đó. Vì vậy S3 phải có nhãn của chủ shop và đo kappa theo từng tenant trước khi điểm giám khảo được dùng để đề xuất bật tự trả lời (ADR-0037).

Chạy lại: `uv run python backend/evals/run_judge_eval.py --out report.md`.
