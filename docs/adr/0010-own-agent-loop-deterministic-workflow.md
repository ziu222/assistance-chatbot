# ADR-0010: Tự viết vòng agent, workflow xác định, LLM chỉ ở chỗ cần

- Status: accepted
- Date: 2026-11-23 (ghi lại quyết định đã áp dụng từ bước 2.5)

## Context

Có ba cách dựng agent: dùng dịch vụ agent có sẵn (Managed Agents của Anthropic), dùng framework (LangGraph…), hoặc tự viết vòng gọi Messages API. Atlas cần nhìn thấy từng prompt, token, lý do gọi tool, và giữ hội thoại, tenant, audit trong DB của mình. Ngoài ra, `PROJECT_CONTEXT` yêu cầu tự viết vòng agent để học và kiểm soát.

## Decision

1. **Tự viết vòng agent** trên `LLMPort` trung lập (manual tool loop), không framework, không dùng Managed Agents.
2. **Workflow xác định, LLM chỉ ở chỗ cần:** cổng (kill switch, mode, trần chi phí, sampling) → ngữ cảnh dựng từ DB → rule rẻ (câu chào, đòi gặp người) → **một** lần phân loại (structured output) → vòng tool read-only có ngân sách (≤ 3 lần gọi model, ≤ 5 tool, wall clock, trần chi phí mỗi run) → kiểm tra bằng code (nguồn, số tiền, URL/HTML, độ dài) → lưu có điều kiện.
3. **Thứ tự tin cậy:** quy tắc xác định > grounding theo tool > lời tự đánh giá của model. Model tự khai độ tự tin không bao giờ là tín hiệu duy nhất.
4. **Mỗi bước ghi vào `agent_run_steps`** đã chiếu/redact (không lưu prompt đầy đủ), nên trace giải thích được mà không lộ dữ liệu khách.

## Consequences

- Phải tự xử lý failover, retry, ngân sách và trace; đổi lại, mọi hành vi đều test được bằng `FakeLLM` và đo được bằng eval.
- Eval 2.12 cho thấy giới hạn 3 lần gọi model không đủ cho đường `search_products` → `get_product` (`budget_exceeded`). Nới ngân sách là quyết định chi phí riêng, cần số đo.
- Không phụ thuộc vào trạng thái phía nhà cung cấp (ADR-0030), nên đổi provider không làm "bot quên".
