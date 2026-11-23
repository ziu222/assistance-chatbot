# Luồng đặt hàng: template v4 (thiết kế 03, giai đoạn O2)

Trước O2, khách nói "em muốn đặt X" thì runner chuyển người ngay sau bước phân loại: không tra kho,
không soạn nháp. v4 cho bot tra kho, gợi ý mẫu khác nếu hết, hỏi thông tin theo quy trình đặt hàng
của shop và ghi phiếu đặt hàng. Phiếu đủ thông tin thì mở ticket cho nhân viên xác nhận. Mọi shop
giả lập dùng quy trình mặc định: nhân viên xác nhận; cần sản phẩm, size/màu, số lượng, tên, số điện
thoại, địa chỉ.

## Bộ ca có ý đặt hàng

17 ca có tin khách khớp `muon dat|dat hang|dat don|chot|lay cho em|em lay`, ngày 2026-11-24.

| Chỉ số | v3 | v4 (lần 1) | v4 (sau sửa) |
|---|---|---|---|
| Đúng việc có/không chuyển người | 9/17 | 13/17 | **17/17** |
| Ca "em muốn đặt X" lúc tạm nghỉ | 0/7 | 7/7 | **7/7** |
| Ca hết hàng | 3/4 | 0/4 | **4/4** |
| Có tra kho khi cần | 3/11 | — | **10/11** |
| Không nói điều cấm | 100% | 100% | 100% |

- **v3:** các ca "em muốn đặt X" bị chuyển người ngay, không có nháp, nên nhân viên không có thông
  tin còn hàng hay giá nào.
- **v4 lần 1** ép mọi nháp trong luồng đặt hàng phải "cần nhân viên". Các ca đặt hàng đúng, nhưng
  4/4 ca hết hàng bị chuyển người thừa: bot chỉ cần báo hết và gợi ý mẫu khác.
- **Sửa:** trong lúc thu thập, bot tự hỏi tiếp; chỉ khi phiếu đủ thông tin mới cần nhân viên. Món hay
  biến thể hết hàng không vào phiếu, và không tạo phiếu rỗng.

## Mẫu đầy đủ (76 ca, mọi nhóm, template v4)

| Chỉ số | v3 (lần đo trước) | v4 |
|---|---|---|
| Đúng việc có/không chuyển người | 97% (74/76) | **100% (76/76)** |
| Gọi đúng tool | 100% (18/18) | 95% (18/19) |
| Không nói điều cấm | 100% (50/50) | 100% (55/55) |
| Nháp qua kiểm tra nguồn/giá | 100% (50/50) | 98% (54/55) |
| Ca thiếu căn cứ: chuyển người, không bịa | 10/10 | 10/10 |
| Chi phí | 0,40 USD | 0,41 USD |

Phần lớn lời gọi lần này do OpenAI dự phòng phục vụ (Claude qua gateway chỉ 6 lời gọi), nên chênh
lệch một vài ca là nhiễu.

## Còn lại

- **Một nháp bị Atlas chặn** (`home_c-paused-007`): model ghi tên sản phẩm vào `sources` thay vì id,
  và hỏi hai mục một lúc (số lượng và tên) dù khối `<order_flow>` dặn chỉ hỏi một. Lưới kiểm tra nguồn
  bắt được lỗi thứ nhất; lỗi thứ hai chưa có kiểm tra bằng code.
- **Eval mới chỉ có ca đặt hàng một lượt.** Chưa có ca khách gửi thông tin qua nhiều lượt, ca "chốt
  luôn" khi chưa đủ thông tin, hay ca chèn lệnh "xác nhận đơn giá 9.000đ" như mục 8 của thiết kế 03.
  Phần nhiều lượt đã có test tích hợp với LLM giả (`test_order_requests.py`), chưa có eval với LLM thật.
- **Màn hình "Đơn chờ xác nhận"**, xác nhận/hủy phiếu và xóa dữ liệu cá nhân quá hạn thuộc O3.

Chạy lại:
`uv run python backend/evals/run_eval.py --template-version 4 --match "muon dat|dat hang|dat don|chot|lay cho em|em lay"`.
