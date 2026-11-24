# ADR-0038: Bot học từ chữ nhân viên qua ứng viên được duyệt, eval và bản phát hành tri thức

- Status: proposed (thiết kế `docs/design/05-learning.md`, chưa implement)
- Date: 2026-11-25

## Context

Bước 3 đo được chất lượng nháp theo ý định, nhưng chưa có đường nào để nháp tốt lên. Ở chế độ Shadow, nhân viên không thấy nháp và gõ lại từ đầu. Mỗi lần nhân viên sửa một con số hay thêm một chi tiết là một bài học bị bỏ phí.

ADR-0030 đã quyết rằng tri thức nằm trong Postgres và bài học phải đi qua `correction → candidate → duyệt → eval → knowledge_release`. E17 quy định ứng viên chỉ dựng từ chữ nhân viên và dữ liệu shop, không bao giờ từ chữ khách. Hiện chưa có chế độ hiển thị nháp, chưa có bản ghi sửa, chưa có bản phát hành tri thức: FAQ được sửa trực tiếp và có hiệu lực ngay.

Rủi ro của "tự học" là sai một lần nhân lên thành sai hàng loạt: nhân viên báo giá cũ, chính sách tạm thời, hay chữ khách cố tình cài vào (đầu độc tri thức).

## Decision

1. **Chế độ `learning`:**
   - Nháp hợp lệ mới nhất hiện trong ô soạn tin với ba nút Dùng / Sửa / Bỏ.
   - Người vẫn là người gửi: tin đi qua API trả lời của nhân viên kèm `draft_id`, và agent runtime vẫn không ghi `messages`.
2. **`draft_uses`:** mỗi nháp ghi `as_is` / `edited` / `discarded`, `edit_ratio` và `diff_features` do code tính. Không chép chữ; chỉ tham chiếu `message_id`. Cặp so sánh của nháp đã hiện mang `draft_exposure = 'shown'` và được đo bằng mức sửa, không trộn với số đo mù.
3. **Bản phát hành tri thức:**
   - `knowledge_releases` lưu ảnh chụp toàn bộ FAQ/tài liệu đã duyệt, kèm con trỏ `tenant_knowledge`.
   - `ContextBuilder` đọc ảnh chụp; `knowledge_hash` tính trên ảnh chụp.
   - Chủ shop sửa tay ⇒ phát hành ngay. Bài học ⇒ chỉ phát hành sau eval.
   - Quay lại = đổi con trỏ.
4. **Ứng viên chỉ từ chữ nhân viên và dữ liệu shop:**
   - Cần ít nhất 2 lần sửa cùng hướng.
   - Route `learning` riêng, có trần chi phí riêng.
   - Kiểm tra bằng code: số tiền, thời hạn, link phải có trong bằng chứng; không có dữ liệu cá nhân; không có chuỗi dài trùng chữ khách.
5. **Không áp dụng tự động:** chủ shop duyệt (hoặc sửa rồi duyệt), rồi cổng eval chạy ca hồi quy của bài học, các ca hồi quy cũ và mẫu ca chuẩn. Đạt mới phát hành; hỏng thì trả lại kèm ca hỏng.
6. **Mỗi bài học là một ca hồi quy** trong `eval_cases`, chạy lại khi đổi model, prompt, template hay route.

## Alternatives considered

- **Tự áp dụng sửa của nhân viên vào FAQ.** Nhanh, nhưng một lần nhân viên sai hay một câu đùa thành tri thức cho mọi khách. Không có điểm dừng để người chịu trách nhiệm xem.
- **Học từ toàn bộ hội thoại, kể cả chữ khách** (RAG trên lịch sử). Tăng độ phủ, nhưng mở đường cho đầu độc tri thức và đưa dữ liệu cá nhân vào prompt chung. Trái E17.
- **Fine-tune định kỳ.** Đắt, khó quay lại, không xóa được dữ liệu cá nhân đã vào trọng số. Đã loại ở ADR-0030.
- **Sửa FAQ trực tiếp như hiện nay, không có bản phát hành.** Đơn giản, nhưng không quay lại được và không biết run nào dùng tri thức nào. Bước 3 cần đúng phiên bản tri thức để đếm lại từ đầu khi tri thức đổi.

## Consequences

- Thêm các bảng `draft_uses`, `knowledge_releases`, `tenant_knowledge`, `knowledge_candidates`, `eval_cases`, giá trị `agents.mode = 'learning'`, route `learning` và trần chi phí của nó.
- `ContextBuilder` đổi nguồn từ bảng FAQ sang ảnh chụp bản phát hành. Phải giữ đúng bất biến "cùng dữ liệu ⇒ cùng `LLMRequest`" và test snapshot.
- Inbox có thêm phần nháp (UI thử nghiệm); API trả lời của nhân viên nhận thêm `draft_id`.
- Eval trở thành một phần của đường phát hành tri thức, nên có chi phí LLM nền.
- Bước 3 tách số đo mù / đã thấy; mức sẵn sàng phải ghi rõ dùng nguồn nào.

## Conditions to revisit

- Tỉ lệ dùng nguyên văn cao bất thường kèm mâu thuẫn số tiền sau khi gửi ⇒ có dấu hiệu nhân viên bấm Dùng mà không đọc; cân nhắc yêu cầu xác nhận khi nháp có số tiền.
- Chủ shop không kịp duyệt, hàng ứng viên dồn ⇒ gom ứng viên tương tự, hoặc cho admin duyệt theo ý định.
- Ảnh chụp bản phát hành vượt khoảng 1 MB ⇒ chuyển sang bảng phiên bản từng mục.
