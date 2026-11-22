# ADR-0032: Tín hiệu leo thang chỉ ghi và đo, chưa điều khiển

- Status: accepted
- Date: 2026-11-23

## Context

Chủ shop muốn bot tự nhận ra khách đang mất kiên nhẫn, hoặc cuộc chat cần con người quyết định. Bằng chứng bên ngoài cho thấy làm tự động việc này dễ sai: COLING 2025 báo cáo luật từ khóa có recall khoảng 0,01, LLM zero-shot có recall cao nhưng precision chỉ quanh 0,7, và độ đồng thuận giữa người gán nhãn chỉ kappa 0,48. Intercom cảnh báo leo thang quá rộng làm tỉ lệ tự giải quyết tụt. Chat bán hàng tiếng Việt lại ngắn, viết tắt và hay thiếu dấu.

## Decision

1. **Chỉ chuyển người cứng khi có tín hiệu xác định** (nút, cụm từ đòi gặp người, chủ đề shop đánh dấu; ADR-0031, bước 2.8).
2. **Tín hiệu hành vi** là hàm thuần trong `agent_runtime/signals.py`: hỏi lặp lại (độ giống trigram trên chữ đã bỏ dấu), phủ nhận câu trả lời, nhắn dồn, nhiều lượt chưa xong, dấu "!!"/"??".
3. **Tín hiệu ngữ nghĩa** thêm hai trường `frustration` và `human_decision` vào `ClassifyOut` có sẵn: không thêm lần gọi LLM. Prompt đổi nên tăng `PROMPT_VERSION`. Tín hiệu chỉ được tính khi hội thoại có ≥ 4 lượt.
4. **Điểm có trọng số** được ghi vào trace dưới dạng step `signal` (cần migration nới CHECK của `agent_run_steps.kind`). Run vẫn chạy như cũ: không mở ticket, không đổi `handling`.
5. **Đo trước khi tin**: `GET /api/v1/escalation/report` so điểm cao nhất của mỗi hội thoại với nhãn thật (có ticket và nhân viên không đánh dấu "không cần"), cho từng độ nhạy. Độ nhạy (`low`/`medium`/`high`, mặc định `low`) nằm trong cấu hình chính sách, hiện chỉ dùng để xem số.
6. **Bộ demo có nhãn + script** (`backend/evals/escalation_demo.json`, `backend/scripts/eval_escalation.py`) đo precision, recall và độ trễ; kết quả ở `docs/evals/escalation-signals.md`.

## Consequences

- Lần đo đầu trên bộ demo tổng hợp cho thấy tín hiệu hành vi một mình yếu, còn điều kiện "≥ 4 lượt" làm mất phần lớn recall của tín hiệu ngữ nghĩa. Đề xuất bỏ điều kiện này cho `human_decision` đã được ghi lại, nhưng chưa làm cho tới khi có nhãn thật.
- Trọng số và ngưỡng hiện là giá trị khởi điểm. Chỉ đổi dựa trên báo cáo từ ticket thật, không dựa trên bộ demo do chính người viết luật soạn.
- Cho điểm điều khiển (nâng ưu tiên ticket, hiện nút gợi ý cho khách) là một quyết định sau, cần ADR mới kèm số đo thật.
