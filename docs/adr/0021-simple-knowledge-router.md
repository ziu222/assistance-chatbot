# ADR-0021: Knowledge router đơn giản — catalog qua tool, FAQ/tài liệu đưa nguyên vào context

- Status: accepted
- Date: 2026-11-23 (ghi lại quyết định đã áp dụng từ bước 2.3)

## Context

Shop nhỏ có ít tri thức: vài chục câu FAQ, một trang chính sách, catalog vài chục đến vài trăm sản phẩm. RAG chunk + embedding thêm hạ tầng và thêm một nguồn lỗi (truy hồi sai), trong khi lượng tri thức này vừa đủ đưa nguyên vào prompt và được cache.

## Decision

1. **Catalog có cấu trúc, truy cập qua tool read-only** `search_products` / `get_product`. Tìm không dấu bằng `pg_trgm` trên chuỗi đã bỏ dấu ở Python (`unaccent` của Postgres không IMMUTABLE nên không index được). Câu hỏi chứa mã SKU thì sản phẩm đó luôn khớp và đứng đầu (sửa sau eval 2.12). Luôn lọc `tenant_id` và `active`; tenant lấy từ run, không bao giờ từ model.
2. **FAQ đã duyệt và tài liệu nhỏ (≤ 20.000 ký tự mỗi tài liệu) đưa nguyên vào khối tri thức** của prompt, sắp theo id, nằm trong phần được cache (ADR-0028). Mỗi run ghi `knowledge_hash`.
3. **Provenance bắt buộc:** nháp chỉ được trích nguồn là id FAQ/tài liệu có trong prompt hoặc sản phẩm tool đã trả. Số tiền chỉ hợp lệ khi khớp giá tool trả hoặc số tiền trong FAQ/tài liệu. Sai ⇒ `grounded = false`.
4. **Ngưỡng xem lại:** khi tri thức một tenant vượt khoảng 30k token thì xem lại quyết định này (RAG theo chủ đề).

## Consequences

- Không lỗi truy hồi cho FAQ/chính sách; chi phí phần lặp thấp nhờ cache.
- Catalog lớn (hàng nghìn SKU) hoặc tài liệu dài sẽ cần chiến lược khác; nguồn catalog từ sàn đi qua adapter (ADR-0034) nhưng vẫn truy cập qua cùng tool.
