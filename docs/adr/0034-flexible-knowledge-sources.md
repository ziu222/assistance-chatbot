# ADR-0034: Tri thức không bắt buộc đầy đủ; catalog qua adapter từng nguồn

- Status: proposed (thiết kế bước 2.13–2.14, chưa implement)
- Date: 2026-11-23

## Context

Atlas nhắm tới shop online nhỏ ở Việt Nam. Rất ít shop có sẵn một file chính sách đầy đủ: nhiều shop chỉ có vài câu FAQ chép từ trang Shopee, có shop chưa viết gì. Catalog cũng đến từ nhiều nơi: shop tự nhập, CSV, Google Sheets, Shopee, TikTok Shop. Dữ liệu sàn có tiêu đề nhồi từ khóa, emoji, hashtag, và đôi khi ghi "giá gốc" cao hơn giá bán.

Thiết kế bước 2 hiện coi catalog là một lần import CSV và ngầm giả định shop có FAQ/tài liệu. Nếu giữ giả định đó, hoặc phải bắt chủ shop chuẩn bị đủ tài liệu trước khi dùng (mất khách non-tech), hoặc bot sẽ đoán khi thiếu căn cứ (rủi ro bịa chính sách và giá).

## Decision

1. **Không bắt buộc tri thức đầy đủ.** Thiếu căn cứ là trạng thái bình thường: bot trả `needs_human` với `reason = "no_source"`, như bất biến hiện tại.
2. **Catalog đến qua adapter `CatalogSource` cho từng nguồn** (manual, csv, sheets; shopee, tiktok là tùy chọn). Adapter trả dữ liệu thô có `external_id`; hàm thuần `normalize` chuyển thành `CatalogItem`; upsert idempotent theo `(tenant_id, source, external_id)`. Mỗi sản phẩm chỉ có một nguồn sở hữu. Sản phẩm biến mất khỏi nguồn bị ẩn (`active = false`), không bị xóa.
3. **Giữ dữ liệu gốc để hiển thị, chuẩn hóa riêng để tìm kiếm.** Giá là giá bán. "Giá gốc", "freeship", mã giảm giá trong nội dung sàn không phải chính sách và không phải số tiền hợp lệ.
4. **Độ phủ tri thức là dẫn xuất** (ADR-0030): FAQ/tài liệu gắn chủ đề trong một danh sách cố định; từ đó dựng dòng "Shop chưa có thông tin về: …" trong khối tri thức của prompt, và màn hình cho chủ shop thấy chủ đề nào còn thiếu.
5. **Chỗ thiếu thành gợi ý**: mỗi run `no_source` ghi `learning_signals` loại `knowledge_gap` (không chép lời khách). Tín hiệu được gộp theo tuần để gợi ý chủ shop viết FAQ, và chủ shop luôn duyệt.
6. **Sàn chỉ qua API chính thức** với ủy quyền OAuth của shop; không cào trang.

## Consequences

- Shop dùng được ngay với bất kỳ mức tài liệu nào; chất lượng tăng dần khi shop bổ sung theo gợi ý.
- Eval phải tách theo hồ sơ shop (đủ / viết dở / chỉ FAQ / không tài liệu / nguồn sàn). Bộ `backend/evals/sim_shop/` đã có 12 hồ sơ này.
- Thêm bảng `catalog_sources`, `catalog_sync_runs`, và cột `source`/`external_id`/`last_seen_at` cho `catalog_items`, `topics` cho FAQ/tài liệu.
- Adapter sàn phụ thuộc việc duyệt ứng dụng đối tác và schema của sàn, nên tách thành bước tùy chọn 2.14.
- Dữ liệu sàn đổi giá theo giờ: cần đo tỉ lệ trả lời trên catalog cũ (`stale_catalog`) trước khi bot tự gửi báo giá ở bước 5.
