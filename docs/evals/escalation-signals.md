# Đo tín hiệu leo thang (bước 2.10)

- Ngày: 2026-11-23; `PROMPT_VERSION` 2026-11-23.1
- Dữ liệu: `backend/evals/escalation_demo.json`, 30 ca TỔNG HỢP (14 cần người, 16 không). Người viết luật cũng viết ca thử ⇒ số đo lạc quan, chỉ để so các phương án; phải đo lại trên hội thoại thật.
- Latency tín hiệu hành vi (Python thuần, mỗi lượt): p50 29 µs, p95 63 µs.

### Chỉ tín hiệu hành vi

| Ngưỡng | Độ nhạy | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| ≥ 1 |  | 5 | 5 | 9 | 11 | 50% | 36% |
| ≥ 2 |  | 4 | 1 | 10 | 15 | 80% | 29% |
| ≥ 3 | high | 0 | 0 | 14 | 16 | — | 0% |
| ≥ 4 | medium | 0 | 0 | 14 | 16 | — | 0% |
| ≥ 5 | low | 0 | 0 | 14 | 16 | — | 0% |
| ≥ 6 |  | 0 | 0 | 14 | 16 | — | 0% |

- Bước phân loại thật (model phục vụ: openai:gpt-4.1-mini; 4/30 lần phải chờ đích chính hỏng rồi mới failover; lý do: anthropic:circuit_open, anthropic:timeout): tất cả p50 1177 ms, p95 31118 ms; bỏ các lần failover p50 1161 ms, p95 1400 ms. tổng chi phí $0.0089 cho 30 lần gọi. Tín hiệu ngữ nghĩa không thêm lần gọi LLM nào: độ trễ thêm vào run ≈ 0 ngoài vài token ra.

### Ngữ nghĩa (chỉ tính khi ≥ 4 lượt, như runner)

| Ngưỡng | Độ nhạy | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| ≥ 1 |  | 4 | 0 | 10 | 16 | 100% | 29% |
| ≥ 2 |  | 4 | 0 | 10 | 16 | 100% | 29% |
| ≥ 3 | high | 4 | 0 | 10 | 16 | 100% | 29% |
| ≥ 4 | medium | 1 | 0 | 13 | 16 | 100% | 7% |
| ≥ 5 | low | 1 | 0 | 13 | 16 | 100% | 7% |
| ≥ 6 |  | 0 | 0 | 14 | 16 | — | 0% |

### Ngữ nghĩa, bỏ điều kiện ≥ 4 lượt

| Ngưỡng | Độ nhạy | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| ≥ 1 |  | 14 | 0 | 0 | 16 | 100% | 100% |
| ≥ 2 |  | 14 | 0 | 0 | 16 | 100% | 100% |
| ≥ 3 | high | 13 | 0 | 1 | 16 | 100% | 93% |
| ≥ 4 | medium | 2 | 0 | 12 | 16 | 100% | 14% |
| ≥ 5 | low | 2 | 0 | 12 | 16 | 100% | 14% |
| ≥ 6 |  | 0 | 0 | 14 | 16 | — | 0% |

### Hành vi + ngữ nghĩa (đúng như runner ghi)

| Ngưỡng | Độ nhạy | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| ≥ 1 |  | 6 | 5 | 8 | 11 | 55% | 43% |
| ≥ 2 |  | 6 | 1 | 8 | 15 | 86% | 43% |
| ≥ 3 | high | 4 | 0 | 10 | 16 | 100% | 29% |
| ≥ 4 | medium | 3 | 0 | 11 | 16 | 100% | 21% |
| ≥ 5 | low | 2 | 0 | 12 | 16 | 100% | 14% |
| ≥ 6 |  | 1 | 0 | 13 | 16 | 100% | 7% |

### Từng ca

| Ca | Nhãn | Hành vi | frustration | human_decision | ms | Model |
|---|---|---|---|---|---|---|
| calm-price | — | — | none | False | 31435 | openai:gpt-4.1-mini (failover) |
| calm-nodiacritic | — | — | none | False | 31038 | openai:gpt-4.1-mini (failover) |
| calm-followup | — | — | none | False | 31313 | openai:gpt-4.1-mini (failover) |
| excited-bangs | — | shouting | none | False | 1402 | openai:gpt-4.1-mini (failover) |
| khong-normal | — | — | none | False | 1127 | openai:gpt-4.1-mini (failover) |
| many-msgs-normal | — | burst | none | False | 1184 | openai:gpt-4.1-mini (failover) |
| long-normal | — | long_unresolved | none | False | 1472 | openai:gpt-4.1-mini (failover) |
| thanks | — | — | none | False | 990 | openai:gpt-4.1-mini (failover) |
| ask-again-different | — | — | none | False | 1256 | openai:gpt-4.1-mini (failover) |
| policy-info | — | — | none | False | 1037 | openai:gpt-4.1-mini (failover) |
| shipping-fee | — | shouting | none | False | 1055 | openai:gpt-4.1-mini (failover) |
| store-hours | — | — | none | False | 962 | openai:gpt-4.1-mini (failover) |
| sale-question | — | — | none | False | 1171 | openai:gpt-4.1-mini (failover) |
| color-question | — | — | none | False | 960 | openai:gpt-4.1-mini (failover) |
| repeat-unanswered | cần người | repeat | mild | True | 1156 | openai:gpt-4.1-mini (failover) |
| negation-angry | cần người | negation | mild | True | 1085 | openai:gpt-4.1-mini (failover) |
| burst-angry | cần người | negation, burst | high | True | 1268 | openai:gpt-4.1-mini (failover) |
| bot-loop | cần người | negation, long_unresolved | mild | True | 1077 | openai:gpt-4.1-mini (failover) |
| refund-polite | cần người | — | mild | True | 951 | openai:gpt-4.1-mini (failover) |
| exception-polite | cần người | — | mild | True | 1292 | openai:gpt-4.1-mini (failover) |
| discount-bulk | cần người | — | none | True | 1206 | openai:gpt-4.1-mini (failover) |
| complaint-staff | cần người | — | mild | True | 1194 | openai:gpt-4.1-mini (failover) |
| wrong-item-calm | cần người | — | mild | True | 1151 | openai:gpt-4.1-mini (failover) |
| frustrated-short | cần người | — | mild | True | 1211 | openai:gpt-4.1-mini (failover) |
| nodiacritic-angry | cần người | negation, shouting | mild | True | 1142 | openai:gpt-4.1-mini (failover) |
| cancel-paid | cần người | — | mild | True | 1132 | openai:gpt-4.1-mini (failover) |
| warranty-broken | cần người | — | mild | True | 1400 | openai:gpt-4.1-mini (failover) |
| threat-review | cần người | — | high | True | 1197 | openai:gpt-4.1-mini (failover) |
| size-advice | — | — | none | False | 31118 | openai:gpt-4.1-mini (failover) |
| repeat-normal-confirm | — | repeat | none | False | 1167 | openai:gpt-4.1-mini (failover) |

## Đọc số (viết tay, 2026-11-23)

- **Hành vi một mình yếu.** Điểm tối đa trên bộ này là 2, nên không bao giờ chạm ngưỡng của bất kỳ độ nhạy nào (3–5). Ở ngưỡng ≥ 2: precision 80%, recall 29%. Phần lớn ca cần người là yêu cầu lịch sự ngay tin đầu (hoàn tiền, bảo hành, giá sỉ), không có dấu hiệu hành vi nào.
- **Ngữ nghĩa mạnh trên bộ tổng hợp, nhưng điều kiện "≥ 4 lượt" làm mất gần hết recall.** Bỏ điều kiện này: ngưỡng ≥ 3 cho precision 100%, recall 86%. Giữ điều kiện như runner hiện tại: recall chỉ còn 29%. Đề xuất (CHƯA làm, chờ dữ liệu thật): chỉ áp điều kiện số lượt cho `frustration`, không áp cho `human_decision`, vì "cần người quyết" phụ thuộc nội dung yêu cầu chứ không phụ thuộc độ dài hội thoại.
- **Ngưỡng `low`/`medium` (5/4) quá chặt với dữ liệu này** (recall 14–21% ở điểm gộp). Không chỉnh trên bộ do chính người viết luật soạn; quyết định sau khi có nhãn thật từ ticket.
- **Model đo được là `gpt-4.1-mini` (đích dự phòng), không phải Claude Sonnet 5.** Gateway Claude dùng để thử không phản hồi (timeout khoảng 31 s rồi circuit breaker mở). Kết quả ngữ nghĩa của Claude **chưa được đo**. Lần đo này cũng lộ ra một vấn đề vận hành: một lần failover làm bước phân loại mất khoảng 31 s. Cần xem lại timeout của route `classify` trước khi bot trả lời khách thật.
- **Độ trễ:** tín hiệu hành vi khoảng 13 µs mỗi lượt. Tín hiệu ngữ nghĩa nằm trong lần phân loại sẵn có (p50 khoảng 1,2 s khi không phải failover), không thêm lần gọi LLM nào. Chi phí khoảng $0,0003 mỗi lần phân loại với `gpt-4.1-mini`.
- **Giới hạn:** 30 ca tổng hợp, một người viết, một lần chạy (LLM không xác định nên số có thể dao động). Không được dùng các con số này để bật tự chuyển người.
