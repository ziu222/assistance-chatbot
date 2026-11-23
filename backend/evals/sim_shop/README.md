# Sim shop eval data

Bộ dữ liệu tạo thế giới shop Việt Nam giả lập cho eval runner bước 2.12. Dữ liệu lấy
cảm hứng từ cấu trúc retail của [τ²-bench](https://github.com/sierra-research/tau2-bench),
MIT License; không sao chép nội dung hoặc dữ liệu của họ.

## Chạy

Từ thư mục repo:

```text
uv run python backend/evals/sim_shop/generate.py --seed 42
uv run python backend/evals/sim_shop/validate.py
```

Generator không gọi mạng/LLM, dùng `random.Random(seed)` và JSON key ổn định nên cùng seed
cho cùng byte đầu ra. `validate.py` import parser catalog, `BusinessHours`, `PolicyConfig` và
`extract_amounts` của Atlas. Trường `eval_seeds` trong template hiện chỉ là đường dẫn quy ước;
chưa có code runtime đọc trường này.

Bộ hiện có 4 shop, 80 SKU/shop, 50 khách/shop, 100 đơn/shop và 450 nhiệm vụ (25 mỗi nhóm
category). Câu không dấu chiếm ít nhất một nửa. Các số tiền là giá một SKU; không chấm tổng
nhiều món vì Atlas chưa hỗ trợ.

Giới hạn: câu sinh theo mẫu sạch hơn chat thật; trạng thái đơn chỉ là dữ liệu chuẩn bị cho
tool tương lai, nên câu hỏi đơn/hủy/hoàn tiền vẫn gắn `needs_human`. Thông tin không có nguồn
được gắn `unclear` và chuyển người, không tự bịa.
