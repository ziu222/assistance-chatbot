# ADR-0037: Đo nháp shadow bằng câu nhân viên thật; giám khảo LLM chỉ được tin sau khi hiệu chỉnh

- Status: proposed (thiết kế `docs/design/04-shadow-comparison.md`, chưa implement)
- Date: 2026-11-24

## Context

Bước 2 soạn nháp cho mọi tin của khách ở chế độ Shadow; nhân viên không thấy nháp và trả lời như bình thường. Số đo chất lượng hiện chỉ đến từ bộ shop giả lập. Bước 5 (tự trả lời theo ý định) cần biết, cho **từng shop** và **từng loại câu hỏi**, nháp của bot có gửi được thay câu nhân viên hay không.

Mỗi lượt nhân viên trả lời một tin mà bot đã soạn nháp là một cặp so sánh có sẵn. Nhưng cả hai phía đều không hoàn hảo:

- câu nhân viên có thể sai hoặc lỗi thời;
- LLM làm giám khảo có thể thiên vị (câu dài, giọng giống nó, chính model của nó);
- khi bước 4 hiển thị nháp cho nhân viên, câu nhân viên không còn độc lập với nháp.

## Decision

1. **Ghép cặp xác định:** nháp mới nhất trước câu trả lời của nhân viên. Câu tham chiếu là các tin `human` liên tiếp sau `based_on_sequence`, tới tin khách kế tiếp hoặc tối đa 10 phút. Nháp cũ hơn trong cụm là `superseded`; không ai trả lời trong 24 giờ là `unanswered`.
2. **Ghi `draft_exposure` cho mọi cặp.** Số đo khi nhân viên không thấy nháp (`hidden`) và khi thấy (`shown`) không bao giờ được gộp.
3. **Chấm bằng code trước:** số tiền, sản phẩm, link, chuyển người, nháp đã bị chặn. Mâu thuẫn số tiền hay sản phẩm là kết luận `wrong` không cần LLM. Giá nhân viên nêu mà không có trong catalog hay tri thức của run thì cặp bị gắn `reference_suspect` và không tính là bot sai.
4. **Giám khảo LLM:**
   - Route `judge` riêng, dùng model khác model soạn nháp.
   - Output có cấu trúc: `verdict`, `sendable`, `missing_info`, `contradicts`, `tone_ok`, và `rationale` ngắn không trích dữ liệu cá nhân.
   - Có trần chi phí theo tenant mỗi ngày, có lấy mẫu, chạy nền ưu tiên thấp.
5. **Hiệu chỉnh trước khi tin:**
   - Có bộ vàng cố định cho giám khảo trong eval.
   - Đo kappa với nhãn của chủ shop theo từng tenant.
   - Chưa đủ 30 nhãn hoặc kappa dưới 0,6 thì điểm giám khảo chỉ để tham khảo, không dùng để đề xuất bật tự trả lời.
6. **Mức sẵn sàng theo ý định × phiên bản cấu hình AI:** ít nhất 30 cặp trong 14 ngày, sai ≤ 2%, gửi được ≥ 85%, giám khảo đã hiệu chỉnh, không có mâu thuẫn số tiền trong 7 ngày. Đổi phiên bản thì đếm lại từ đầu. Đây chỉ là đề xuất; chủ shop quyết.
7. **Chỉ đọc:** bước 3 không đổi nháp, hội thoại hay đường trả lời. Tín hiệu học chỉ có id, nhãn và tên trường.

## Alternatives considered

- **Chỉ đo bằng bộ giả lập.** Rẻ và lặp lại được, nhưng không phản ánh khách, giọng và chính sách thật của từng shop. Giữ làm lưới chặn hồi quy, không thay cho số đo thật.
- **So độ giống chữ** (BLEU, embedding). Không phân biệt được "khác chữ cùng ý" với "giống chữ sai giá". Số tiền và sản phẩm đã có kiểm tra bằng code chính xác hơn; phần ý nghĩa cần giám khảo.
- **Tin giám khảo ngay.** Nhanh, nhưng không biết nó lệch thế nào so với chủ shop. Quyết định bật tự gửi tin cho khách không nên dựa trên một thước đo chưa ai đo.
- **Cho nhân viên chấm mọi nháp.** Chính xác nhất, nhưng tốn công và làm nhân viên thấy nháp (mất tính độc lập). Chỉ dùng cho mẫu nhỏ để hiệu chỉnh.

## Consequences

- Thêm các bảng `shadow_comparisons` và `judge_labels`, route `judge`, `usage_events.purpose = 'judge'`, một việc định kỳ trong worker, và bộ vàng `backend/evals/judge/`.
- Bước 5 có tiêu chí khách quan theo từng ý định thay vì "cảm thấy ổn".
- Bước 4 nhận danh sách ý định hay sai để ưu tiên học.
- Thêm chi phí LLM nền, có trần và lấy mẫu; chia giới hạn đồng thời của gateway với đường soạn nháp.
- Shop ít tin nhắn có thể mất nhiều tuần mới đủ mẫu; giao diện phải nói rõ "chưa đủ mẫu".

## Conditions to revisit

- Kappa của giám khảo thấp kéo dài ở nhiều tenant ⇒ xem lại rubric hay model giám khảo, hoặc dựa nhiều hơn vào nhãn người.
- Bước 4 hiển thị nháp cho nhân viên ⇒ cần cách đo mới cho số cặp `shown` (ví dụ đo mức sửa thay vì so sánh độc lập).
- Có đủ dữ liệu thật ⇒ cân nhắc dùng các cặp đã gán nhãn (đã ẩn danh) để mở rộng bộ eval.
