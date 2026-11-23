# Gửi link sản phẩm trong luồng đặt hàng (thiết kế 03, giai đoạn O4)

Đo xem bot có gửi đúng link trang sản phẩm khi shop chọn cách "khách tự đặt qua link" hay không,
trên LLM thật, ngày 2026-11-24, template v4.

## Dữ liệu thêm vào bộ giả lập

- Link hư cấu (tên miền `.example`):
  - 5 shop sàn có link sàn (`marketplace_link`);
  - 3 shop có website (`fashion_a`, `home_c`, `tech_d`) có link trang sản phẩm (`product_link`);
  - 4 shop còn lại không có link, giữ cách "nhân viên xác nhận".
- Quy trình đặt hàng của từng shop nằm trong `shop.json` (`order_flow`); eval runner phát hành đúng
  cấu hình đó.
- Nhóm nhiệm vụ mới `order_request` (25 ca), ví dụ "Cho em đặt 2 … size M nhé", "Em muốn mua …".
  - Ca có đủ món, size/màu, số lượng ở shop gửi link, và mọi biến thể còn hàng đủ số lượng, phải
    chứa **đúng nguyên văn** link của sản phẩm (8 ca).
  - Mọi ca đều cấm nói "đặt hàng thành công", "đã xác nhận đơn", "đã lên đơn".
  - Nhóm mới đứng cuối danh sách, nên 490 ca cũ giữ nguyên id và nội dung.
- Chiều chấm mới `link`.

## Kết quả

Bộ ca có ý đặt hàng (46 ca có tin khớp `cho em dat|em lay|chot|muon mua|em dat|muon dat`):

| Lần | Thay đổi | Gửi đúng link | Có tra kho | Không nói điều cấm |
|---|---|---|---|---|
| 1 | O4 như đã merge | **0/9** | 34/40 | 40/40 |
| 2 | Khối `<order_flow>` nêu cách đặt hàng ngay từ đầu, bắt tra kho trong lượt | 8/9 | 36/40 | 39/39 |
| 3 | Sửa nhãn (ca thiếu hàng) và lỗi `]` dính vào link | 7/8 | 36/40 | 39/39 |

- **Lần 1:** khối `<order_flow>` được dựng **trước** khi tra kho. Lúc đó phiếu chưa có món, nên khối
  chỉ bảo bot "tra món rồi hỏi mục tiếp theo" và không hề nói tới link. Bot báo giá, còn hàng rồi
  hỏi xác nhận; ở shop gửi link còn có ca xin tên, số điện thoại, địa chỉ.
- **Sửa:** khối nói cách đặt hàng của shop ngay từ đầu. Với cách gửi link: đủ món, size/màu, số
  lượng thì gửi đúng `url` trong kết quả tool, chép nguyên văn, không hỏi thông tin cá nhân.
- **Ca trượt ở lần 2** là lỗi nhãn: khách đặt 3 cái mà biến thể chỉ còn 2, và bot đúng khi gợi ý mẫu
  khác. Bộ sinh dữ liệu giờ chỉ đòi link khi mọi biến thể còn hàng đủ số lượng.
- **Lỗi thật lộ ra ở lần 2:** bot viết `[Đặt tại đây: https://…]` thì dấu `]` bị tính vào link, làm
  nháp đúng bị chặn. Đã sửa, kèm test.
- **Biến thể:** khách viết "size M màu đen" thì phiếu không khớp được với biến thể "M đen" của
  catalog, nên phiếu không bao giờ đủ thông tin. Giờ biến thể khớp khi mọi từ của nó có trong câu;
  nếu nhiều biến thể khớp ngang nhau thì không chọn, để bot hỏi lại.

Mẫu đầy đủ (80 ca, 4 ca mỗi nhóm, kể cả nhóm mới):

| Chỉ số | Kết quả |
|---|---|
| Đúng việc có/không chuyển người | 100% (80/80) |
| Gửi đúng link | 2/2 |
| Không nói điều cấm | 100% (57/57) |
| Nháp qua kiểm tra nguồn/giá/link | 100% (57/57) |
| Ca thiếu căn cứ: chuyển người, không bịa | 9/9 |
| Chi phí | 0,47 USD |

Phần lớn lời gọi do OpenAI dự phòng phục vụ (Claude qua gateway 0–5 lời gọi mỗi lần chạy).

## Còn lại

- **Bỏ qua tra kho (khoảng 10% ca đặt hàng):** model trả lời "em kiểm tra rồi…" hoặc "xem tại link bên
  dưới" mà không gọi `search_products`, nên không có link để gửi (`home_c-order_request-018`). Atlas
  không để lọt: nháp không có link, hoặc bị chặn nếu bịa. Muốn chắc hơn thì cho runner nhắc model
  tra kho một lần khi kết thúc lượt mà chưa gọi tool, thay vì sửa thêm prompt.
- **Tên sản phẩm kiểu sàn:** model chép nguyên tiêu đề có "[Mã PK83 giảm 30K]" vào nháp; bước kiểm
  tra số tiền chặn nháp đó (`30K` không phải giá hợp lệ). An toàn, nhưng làm mất một nháp đúng. Có
  thể dặn dùng tên ngắn, bỏ phần quảng cáo trong ngoặc.
- **"Em muốn đặt X, shop có trả lời được không?"** đôi khi bị phân loại thêm `human_request` nên
  chuyển người ngay (`tech_d-paused-018`, `home_bx-paused-012`). Câu này mơ hồ; mức lỗi giống các lần
  đo trước.

Chạy lại:
`uv run python backend/evals/run_eval.py --template-version 4 --match "cho em dat|em lay|chot|muon mua|em dat|muon dat"`.
