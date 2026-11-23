# Shop giả lập cho eval (bước 2.12)

Thế giới bán hàng online tiếng Việt, hư cấu nhưng giữ đúng ngữ cảnh shop Việt Nam, dùng để đo
agent (giá, nguồn), tầng chính sách (giờ làm việc, tin tự động) và tín hiệu leo thang. Cấu trúc
lấy cảm hứng từ mảng retail của [τ²-bench](https://github.com/sierra-research/tau2-bench)
(Sierra, giấy phép MIT): chính sách + dữ liệu cửa hàng + nhiệm vụ có đáp án. Không sao chép nội
dung hay dữ liệu của họ.

## Vì sao 12 shop khác nhau

Shop thật hiếm khi có đủ tài liệu. Có shop viết chính sách đầy đủ, có shop chỉ chép vài câu FAQ
từ trang Shopee, có shop chưa viết gì và chỉ có catalog đồng bộ từ sàn. Bot phải chạy được với
mọi mức đó, và khi thiếu căn cứ thì phải nói sẽ nhờ nhân viên thay vì đoán. Bộ dữ liệu vì vậy đa
dạng theo bốn trục:

| Trục | Các mức |
|---|---|
| Ngành | thời trang, mỹ phẩm, đồ gia dụng, điện tử/phụ kiện (mỗi shop chỉ bán đúng ngành) |
| Tài liệu | đủ 8 mục chính sách; chính sách viết dở (thiếu mục); chỉ có FAQ, không có file chính sách; không có tài liệu nào |
| Nguồn catalog | tự nhập; nhập vội (mô tả thiếu chất liệu/xuất xứ); đồng bộ kiểu Shopee; đồng bộ kiểu TikTok Shop |
| Quy mô | 12, 20, 35–80 và 300 SKU |

| Shop | Ngành | Tài liệu | Catalog | SKU |
|---|---|---|---|---|
| Mây Mặc `fashion_a` | thời trang | đủ | tự nhập | 80 |
| Ốc Tee `fashion_ot` | thời trang | FAQ 3 câu (ship), không file chính sách | kiểu Shopee | 60 |
| Gió Chiều `fashion_gc` | thời trang | giờ, ship, thanh toán; **chưa có đổi trả** | kiểu TikTok | 45 |
| Lá Nâu `beauty_b` | mỹ phẩm | đủ | tự nhập | 80 |
| Mộc Hương `beauty_mh` | mỹ phẩm | FAQ ship + đổi trả, không file chính sách | nhập vội | 35 |
| Glow Lab `beauty_gl` | mỹ phẩm | đổi trả, khuyến mãi, thanh toán; **không ghi phí ship** | kiểu TikTok | 50 |
| Nhà Gọn `home_c` | gia dụng | đủ | tự nhập | 80 |
| Bếp Xinh `home_bx` | gia dụng | **không có tài liệu** | kiểu Shopee | 20 |
| Kệ Nhà `home_kn` | gia dụng | đủ | tự nhập | 300 |
| Ghim Số `tech_d` | điện tử | đủ | tự nhập | 80 |
| Phụ Kiện 247 `tech_pk` | điện tử | **không có tài liệu** | kiểu Shopee | 70 |
| Loa Nhỏ `tech_ln` | điện tử | giờ + bảo hành | tự nhập | 12 |

Shop đồng bộ từ sàn có thêm `raw_listing.json` (hư cấu, mô phỏng dữ liệu kéo về: tiêu đề nhồi từ
khóa, emoji, hashtag, model dạng `"đen,M"`, giá bán và đôi khi "giá gốc" cao hơn). `catalog.csv` là
bản đã chuẩn hóa từ file đó, đúng định dạng import của Atlas. `shop.json` ghi `knowledge` (shop đã
viết mục nào, có file chính sách không, bao nhiêu FAQ, nguồn catalog) và `hidden_truth` (sự thật
shop biết nhưng có thể chưa viết ra: **không** phải căn cứ cho bot).

## Chạy

```text
uv run python backend/evals/sim_shop/generate.py --seed 42
uv run python backend/evals/sim_shop/validate.py
```

Generator không gọi mạng hay LLM. Cùng seed cho ra file giống hệt byte. `validate.py` dùng parser
catalog, `BusinessHours`, `PolicyConfig`, `decide`, `detect_handoff`, `extract_amounts` và
`behavior_signals` thật của Atlas; `tests/unit/test_sim_shop.py` chạy nó trong CI.

## Nhiệm vụ

490 nhiệm vụ trong 19 nhóm (25 mỗi nhóm, riêng `missing_info` 40). `expected` gồm `intents_any`,
`tools_any`, `sources_include` (SKU), `amounts`, `must_not_say`, `needs_human`, `reason`,
`policy.situation`, nhãn `escalation`; ca FAQ có `topic`, ca hết hàng có `variant`, ca mơ hồ có
`candidates`. Đáp án luôn tính theo **tri thức shop đó thật sự có**:

- Hỏi chủ đề shop chưa viết (ship, đổi trả, giờ, bảo hành, sỉ, hóa đơn…) ⇒ `needs_human=true`,
  `reason="no_source"`, `must_not_say` là các con số shop khác dùng (bot bịa thường bịa ra chúng).
- Hỏi thuộc tính sản phẩm mà mô tả không có ⇒ `no_source`.
- Tiêu đề/mô tả kiểu sàn có "giá gốc" ⇒ báo giá đúng là giá bán, giá gốc nằm trong `must_not_say`.
- Câu hỏi về đơn hàng ⇒ `needs_human=true` vì agent chưa có tool đơn hàng; đáp án khi có tool
  nằm ở `future_expected`.

`validate.py` chặn các lỗi từng gặp và các lỗi theo độ phủ tri thức: hậu tố giả, câu lặp theo mẫu,
tỉ lệ không dấu thấp, hội thoại nhiều lượt dưới 4 lượt, ca bực tức không bật tín hiệu, ca hết hàng
trỏ vào hàng còn tồn, sản phẩm sai ngành, giá theo bước cố định, chính sách đầy đủ mà quá mỏng,
FAQ lộ thuật ngữ hệ thống, nhãn "không cần người" mà luật cụm từ của Atlas lại chuyển người, nhãn
tình huống khác `decide`, chủ đề shop không có tài liệu mà không gắn `no_source` (và ngược lại),
ca `no_source` mà vẫn có số tiền kỳ vọng, giá gốc không bị cấm, `catalog.csv` lệch
`raw_listing.json`, dưới 10 shop, dưới 2 shop không có tài liệu, dưới 10% ca thiếu căn cứ.

## Giới hạn

- Câu khách ghép từ mẫu viết tay + biến đổi theo persona: vẫn "sạch" hơn chat thật. Bước sau nên
  thêm câu do LLM đóng vai persona sinh ra và trộn ca viết tay khó.
- `raw_listing.json` chỉ mô phỏng kiểu dữ liệu sàn, **không** theo đúng schema API của Shopee hay
  TikTok Shop; adapter thật phải viết theo tài liệu chính thức của từng sàn.
- Ca hỏi thuộc tính mà mô tả không có còn ít (vài ca); tên dòng sản phẩm (Daily, Pro…) là quy ước.
- Giờ trong `at` cố định quanh 24–30/11/2026; tạm nghỉ của mọi shop là sáng 24/11.
- Trường `eval_seeds` trong template là đường dẫn quy ước; chưa có code runtime đọc file seeds.

## Đặt hàng và link sản phẩm (thiết kế 03)

`shop.json` có `order_flow`: shop sàn gửi link sàn, `fashion_a`/`home_c`/`tech_d` gửi link trang sản phẩm, shop còn lại để nhân viên xác nhận. `catalog.csv` và `raw_listing.json` có cột `url` (tên miền `.example`, hư cấu). Nhóm `order_request` (25 ca) chấm thêm chiều `link`: ca đủ món, size/màu, số lượng ở shop gửi link phải chứa đúng link đó.
