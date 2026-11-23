# Shop giả lập cho eval (bước 2.12)

Thế giới bán hàng online tiếng Việt, hư cấu nhưng giữ đúng ngữ cảnh shop Việt Nam, dùng để đo
agent (giá, nguồn), tầng chính sách (giờ làm việc, tin tự động) và tín hiệu leo thang. Cấu trúc
lấy cảm hứng từ mảng retail của [τ²-bench](https://github.com/sierra-research/tau2-bench)
(Sierra, giấy phép MIT): chính sách + dữ liệu cửa hàng + nhiệm vụ có đáp án. Không sao chép nội
dung hay dữ liệu của họ.

## Chạy

```text
uv run python backend/evals/sim_shop/generate.py --seed 42
uv run python backend/evals/sim_shop/validate.py
```

Generator không gọi mạng hay LLM. Cùng seed cho ra file giống hệt byte. `validate.py` dùng parser
catalog, `BusinessHours`, `PolicyConfig`, `decide`, `detect_handoff`, `extract_amounts` và
`behavior_signals` thật của Atlas; `tests/unit/test_sim_shop.py` chạy nó trong CI.

## Cấu trúc

| File | Nội dung |
|---|---|
| `world.py` | Dữ liệu viết tay: sản phẩm theo ngành, chính sách từng shop, mẫu câu khách, persona |
| `generate.py` | Ghép dữ liệu, tính giá/tồn, dựng nhiệm vụ; đáp án tính từ chính dữ liệu đã sinh |
| `shops/<shop>/` | `shop.json`, `policy.md`, `faq.json`, `catalog.csv` (định dạng import của Atlas), `customers.json`, `orders.json` |
| `tasks.json` | 450 nhiệm vụ có đáp án (25 mỗi nhóm × 18 nhóm) |
| `personas.json` | 8 kiểu khách cho simulator sau này |

Bốn shop, mỗi shop chỉ bán đúng ngành của mình: thời trang (Mây Mặc), mỹ phẩm (Lá Nâu), đồ gia
dụng (Nhà Gọn), điện tử và phụ kiện (Ghim Số). Mỗi shop có 80 SKU kèm biến thể size/màu/dung tích,
giá theo khoảng thị trường, 60 khách hư cấu, 120 đơn hàng. Chính sách khác nhau giữa các shop:
đổi trả 3/7/15/30 ngày, phí ship theo vùng, ngưỡng freeship, COD có/không/có phí, giá sỉ, VAT,
bảo hành (gia dụng, điện tử).

## Nhiệm vụ

Mỗi nhiệm vụ có `expected`: `intents_any`, `tools_any`, `sources_include` (SKU), `amounts` (số
tiền đúng), `must_not_say`, `needs_human`, `reason`, `policy.situation`, nhãn `escalation`; ca
hết hàng có `variant`, ca mơ hồ có `candidates`. `notes` nêu căn cứ (SKU, mục chính sách, mã
đơn). Đáp án theo **khả năng hiện tại** của Atlas: agent chỉ có tool tra catalog, nên câu hỏi
về đơn hàng là `needs_human=true`; đáp án khi có tool đơn hàng nằm ở `future_expected`.

`validate.py` chặn các lỗi từng gặp: hậu tố giả để né kiểm trùng, câu lặp theo mẫu (yêu cầu
≥ 65% dạng câu khác nhau), tỉ lệ không dấu < 40%, hội thoại "nhiều lượt" dưới 4 lượt, ca bực tức
không bật tín hiệu hành vi nào, ca hết hàng trỏ vào biến thể còn tồn, sản phẩm sai ngành, giá
theo bước cố định, chính sách dưới 1.000 ký tự, FAQ lộ thuật ngữ hệ thống, nhãn "không cần
người" mà luật cụm từ của Atlas lại chuyển người, nhãn tình huống khác với `decide` thật.

## Giới hạn

- Câu khách ghép từ mẫu viết tay + biến đổi theo persona (bỏ dấu, viết tắt): vẫn "sạch" hơn chat
  thật (ít lỗi chính tả, ít emoji, không có tiếng lóng địa phương nặng). Bước sau nên thêm câu do
  LLM đóng vai persona sinh ra và trộn ca viết tay khó.
- Tên dòng sản phẩm (Daily, Pro, Calm…) là quy ước để có nhiều SKU cùng loại; shop thật đặt tên
  đa dạng hơn.
- Một số ghép mẫu có thể hơi gượng (vd đổi "sai màu" cho sản phẩm không có màu).
- Giờ trong `at` cố định quanh 24–30/11/2026; tạm nghỉ của mọi shop là sáng 24/11.
- Trường `eval_seeds` trong template là đường dẫn quy ước; chưa có code runtime đọc file seeds.
