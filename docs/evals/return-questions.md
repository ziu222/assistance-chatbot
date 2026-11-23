# Hỏi chính sách đổi trả và yêu cầu đổi trả: template v3

Template v2 coi mọi câu nhắc tới đổi/trả là việc cần nhân viên. Vì vậy câu "mua về không vừa thì
đổi được trong bao lâu?" vẫn bị chuyển người, kể cả ở shop đã viết chính sách đổi trả. v3 tách hai
trường hợp này. Agent tạo từ v1/v2 giữ nguyên ảnh chụp template cũ.

## Tiêu chí

- Câu hỏi về chính sách mà FAQ/tài liệu đã có: bot trả lời, `needs_human = false`.
- Yêu cầu đổi/trả/hoàn một món đã mua, hoặc xin ngoại lệ: vẫn chuyển người 100%. Lý do là `policy`
  nếu shop có chính sách đổi trả, không thì `no_source`.
- Shop chưa có chính sách đổi trả: không bao giờ nói "đổi được" (`no_source_safe`, `forbidden`).

Bộ test tập trung: 74 ca có tin khách khớp `doi|tra hang|hoan|tra lai`
(`run_eval.py --match`), trải trên 11 nhóm.

## Các bước (mỗi lần một thay đổi, cùng bộ 74 ca)

| Bước | Thay đổi | Đúng việc chuyển người | Khớp lý do |
|---|---|---|---|
| Baseline | template v2 | 71/74 | 11/14 |
| A | v3: tách HỎI chính sách khỏi YÊU CẦU đổi/trả, kèm 2 ví dụ | **74/74** | 9/15 (`order` cho yêu cầu đổi "món đã mua") |
| B | `DRAFT`: đổi/trả không thuộc `order`; chủ đề nằm trong dòng "Shop chưa có thông tin về" ⇒ `no_source` | 73/74 | 12/16 |
| C | v3: ghi `reason` ngay trong đoạn đổi/trả (`policy` hay `no_source`) | 73/74 | 11/13 |
| D | v3: khách mới báo "em cần hỏi đổi trả" mà chưa nói cụ thể ⇒ hỏi lại, không chuyển người | **74/74** | 8/10 |

Ở mọi bước, `forbidden` và `no_source_safe` đều 100%: không lần nào bot nói "đổi được" ở shop chưa có
chính sách.

## Mẫu đầy đủ (76 ca, mọi nhóm, template v3)

| Chỉ số | v2 (lần đo trước) | v3 |
|---|---|---|
| Đúng việc có/không chuyển người | 99% (75/76) | 97% (74/76) |
| Khớp lý do | 100% (7/7) | 100% (9/9) |
| Không nói điều cấm | 100% | 100% (50/50) |
| Ca thiếu căn cứ: chuyển người, không bịa | 10/10 | 10/10 |
| Chi phí | 0,29 USD | 0,40 USD |

Ca v2 sai (`beauty_gl-faq-012`, hỏi chính sách đổi trả) đã đúng ở v3. Hai ca v3 sai thuộc nhóm "mở
đầu mơ hồ ngoài giờ / tạm nghỉ". Chạy riêng nhóm này hai lần mỗi bản thì v2 đạt 5/11 và 7/11, v3 đạt
6/11 cả hai lần, tức là lỗi sẵn có chứ không phải do v3. Chi phí cao hơn chủ yếu vì lần này Claude
phục vụ nhiều lời gọi hơn (28 so với 9). Model phục vụ thay đổi giữa các lần chạy (gateway Claude chập
chờn, phần lớn lời gọi do OpenAI dự phòng phục vụ), nên chênh lệch một vài ca là nhiễu.

## Còn lại

- **2 ca ở shop tech_ln vẫn gán `policy` thay vì `no_source`** cho yêu cầu đổi hàng, dù prompt có
  dòng "Shop chưa có thông tin về: … đổi trả". Ca vẫn được chuyển người, nhưng không sinh tín hiệu
  `knowledge_gap`. Nếu cần chắc chắn thì nên chặn bằng code (độ phủ biết shop chưa có `returns`), thay
  vì sửa thêm prompt.
- **"Em muốn đặt X, shop có trả lời được không?" lúc tạm nghỉ/ngoài giờ:** bộ phân loại gắn
  `order_intent`, nên ca này được chuyển người, còn nhãn của bộ dữ liệu là không chuyển. Cần quyết
  định nhãn nào đúng: bot không tự chốt đơn, nên chuyển người có thể mới là hành vi mong muốn.
- **Shop chưa viết giờ làm việc:** câu "giờ này còn ai trực không" bị trả `no_source`. Có thể hợp lý,
  vì shop không có thông tin giờ.

Chạy lại: `uv run python backend/evals/run_eval.py --template-version 3 --match "doi|tra hang|hoan|tra lai"`.
