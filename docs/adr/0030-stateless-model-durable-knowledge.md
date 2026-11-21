# ADR-0030: Mô hình không giữ trạng thái; tri thức và ngữ cảnh nằm trong Postgres

- Status: accepted
- Date: 2026-11-22

## Context

Câu hỏi đặt ra khi thiết kế failover (ADR-0015): **nếu Claude sập và hệ thống chuyển sang OpenAI, bot có "quên" những gì nhân viên đã dạy không?**

Câu trả lời phụ thuộc vào việc "bot nhớ" nằm ở đâu. Một LLM API không có trí nhớ: mỗi yêu cầu chỉ biết những gì được gửi kèm. Mọi thứ trông như "bot đã học" thực chất là dữ liệu của chính hệ thống được nhét vào prompt. Riêng các nhà cung cấp có một số trạng thái **gắn với họ** (prompt cache, thinking block chỉ hợp lệ với model đã sinh ra nó, tính năng thread/memory phía server, fine-tune) và những thứ đó không chuyển sang provider khác được.

Nếu để tri thức nằm ở các chỗ đó, failover sẽ làm mất nó. Nếu để ở Postgres và dựng lại cho mỗi yêu cầu, failover là việc vô hại.

## Decision

1. **Mô hình là một hàm thuần**: `LLMRequest` đầy đủ → `LLMResult`. Không dùng tính năng lưu trạng thái phía provider để chứa tri thức hay hội thoại (server-side threads, memory tool, assistants/threads, fine-tuning).

2. **Phân loại mọi trạng thái vào ba nhóm:**

   | Nhóm | Ví dụ | Mất thì sao |
   |---|---|---|
   | **Nguồn sự thật (Postgres)** | tin nhắn, FAQ, tài liệu, catalog, agent version, (bước 4) correction, candidate, knowledge release, eval case | không được mất; có backup (§17.5) |
   | **Dẫn xuất (dựng lại được)** | tóm tắt hội thoại, embedding, prompt cache, kết quả run đang dở | chỉ tốn thêm tiền/độ trễ |
   | **Gắn với provider** | thinking block, cache phía provider, thread/memory của họ | **không được dựa vào**, không lưu như dữ liệu |

3. **`ContextBuilder` xác định và trung lập với provider**: cùng dữ liệu trong DB ⇒ cùng `LLMRequest`, byte giống byte (test snapshot). Failover chỉ đổi adapter; yêu cầu gửi đi không đổi, nên mọi tri thức nằm trong yêu cầu vẫn nguyên.

4. **Hội thoại**: bảng `messages` là sự thật. Context luôn gồm N lượt gần nhất **nguyên văn** (khởi điểm 10). Tóm tắt cuộn (khi hội thoại dài) lưu ở `agent_conversation_state` kèm `through_sequence` và `produced_by`; thiếu hoặc cũ ⇒ bỏ qua và dựng lại từ `messages`. Tóm tắt là **cache**, không phải sự thật; điều khách đã nói (size, ngân sách) phải suy ra lại được từ tin gốc.

5. **Tri thức nhân viên dạy** (bước 3–4): sửa nháp ⇒ `correction record` ⇒ `candidate` ⇒ duyệt ⇒ qua eval ⇒ phát hành thành **`knowledge_release` có version** trong Postgres. `ContextBuilder` đọc release đang hiệu lực (`knowledge_hash` ghi vào mỗi run). Bài học **không bao giờ** được ghi thẳng vào prompt, vào provider hay vào trọng số (`PROJECT_CONTEXT` §26.5, §26.6). Mỗi bài học còn sinh thêm một **eval case hồi quy**, để kiểm chứng nó có thật sự được model nào đó áp dụng.

6. **Đổi model, provider, prompt, template hoặc release là một "thay đổi cấu hình AI"**: phải qua eval **theo từng đích** trước khi vào route. Cùng một tri thức, hai model có thể hành xử khác nhau; failover vì thế an toàn về dữ liệu nhưng chỉ an toàn về chất lượng nếu đích dự phòng đã được đo.

7. **Provider lỗi giữa một bước**: chạy lại *bước đó* trên đích kế từ lịch sử trung lập. Tool của agent đều read-only và idempotent nên chạy lại an toàn; đây là một lý do cụ thể để chưa cho agent dùng tool có side-effect ở bước 2.

## Alternatives considered

- **Thread/memory phía provider**: giảm token gửi lên, nhưng khóa vào một nhà cung cấp, mất khi failover, và không kiểm toán hay xóa theo yêu cầu của khách được (§16.8).
- **Fine-tune để "dạy" bot**: đắt, khó rollback, không xóa được dữ liệu cá nhân đã học vào trọng số; nhân viên sửa một câu mà phải chờ huấn luyện lại.
- **Lưu nguyên prompt/response của mọi run để phát lại**: tốn chỗ và giữ PII vô thời hạn; không cần, vì dựng lại từ nguồn sự thật cho cùng kết quả.

## Consequences

### Positive
- Failover, đổi model hay đổi nhà cung cấp không làm mất tri thức; sửa/xóa một mẩu tri thức có hiệu lực ngay ở mọi provider.
- Xóa dữ liệu khách theo yêu cầu chỉ cần xóa ở Postgres, không còn bản sao ẩn ở provider.
- Có thể phát lại một run để gỡ lỗi mà không cần lưu prompt.

### Negative
- Mỗi yêu cầu gửi lại toàn bộ context ⇒ tốn token; giảm bằng prompt caching (khối ổn định đặt trước), tóm tắt và giới hạn token theo khối.
- Tóm tắt có thể làm rơi chi tiết; vì thế 10 lượt gần nhất luôn nguyên văn.
- Eval phải chạy trên từng đích dự phòng, tốn thêm chi phí.

### Neutral
- Chuyển provider làm prompt cache nguội: lần đầu đắt hơn một chút, không ảnh hưởng tính đúng.

## Conditions to revisit

- Chi phí gửi lại context trở thành khoản lớn: cân nhắc cache ngữ nghĩa (chỉ cho câu trả lời có nguồn là FAQ tĩnh, §19.9) hoặc thread phía provider **chỉ làm tối ưu hiệu năng**, vẫn giữ nguồn sự thật ở Postgres.
- Cần kiến thức hiểu riêng theo khách hàng (hồ sơ khách): thêm bảng facts có nguồn gốc và quyền truy cập, không đưa vào prompt chung.
