# ADR-0028: Bố cục prompt cho prompt caching

- Status: accepted
- Date: 2026-11-23 (ghi lại quyết định đã áp dụng từ bước 2.4–2.5)

## Context

Phần lớn token vào của mỗi run lặp lại giữa các run của cùng shop: định nghĩa tool, quy tắc an toàn, hướng dẫn template, FAQ và chính sách. Prompt caching chỉ hiệu quả khi phần lặp nằm ở đầu và giống nhau từng byte.

## Decision

1. **Thứ tự cố định:** định nghĩa tool (thứ tự cố định) → quy tắc chung và an toàn (đổi theo `PROMPT_VERSION`) → hướng dẫn template + tên shop (đổi theo agent version) → khối tri thức FAQ/tài liệu sắp theo id (đổi theo `knowledge_hash`, đặt điểm cache ở cuối khối này) → hội thoại (bọc `<customer_messages>`, đã escape) → kết quả tool.
2. **Không chèn thời gian hiện tại hay id ngẫu nhiên vào các khối ổn định;** JSON sắp khóa ổn định. Có test snapshot: hai lần dựng cùng dữ liệu ⇒ yêu cầu giống hệt byte.
3. **Adapter tự đặt điểm cache** theo cách của từng provider; `LLMRequest` trung lập không chứa trường riêng của provider (ADR-0015).
4. Dòng "Shop chưa có thông tin về: …" (ADR-0034) nằm trong khối tri thức để vẫn được cache.

## Consequences

- Đổi prompt, template hay tri thức làm nguội cache cho tenant đó (chấp nhận được).
- Prefix quá ngắn (shop rất ít FAQ) không đạt ngưỡng cache tối thiểu của provider; đó là bình thường, không phải lỗi.
- Failover sang provider khác làm cache nguội tạm thời; tri thức không đổi vì yêu cầu được dựng lại từ DB.
