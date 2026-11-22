# ADR-0015: Lớp provider LLM — nhiều nhà cung cấp, failover, dễ bảo trì

- Status: accepted
- Date: 2026-11-22

## Context

Agent (bước 2) gọi LLM để phân loại và soạn nháp. Hiện có hai đường gọi dùng được:

- **Claude** qua UniversalAI Gateway (`https://1gw.gwai.cloud`, chuẩn Claude Messages API, header `x-api-key`). **Gateway chỉ dựng tạm để test**: trước khi deploy sẽ đổi sang key API chính thức, nên mọi thứ liên quan (base URL, key) chỉ là cấu hình môi trường, không có dòng code nào biết tới gateway. Gateway có chèn thêm token ẩn vào yêu cầu, nên số liệu chi phí/eval đo qua nó phải đo lại trên đường chính thức.
- **OpenAI** gọi trực tiếp (key `sk-…`).

Yêu cầu:

| Loại | Yêu cầu |
|---|---|
| Chức năng | Cùng một run có thể được phục vụ bởi Claude hoặc OpenAI; tool calling; structured output; ghi usage/chi phí theo provider; test CI không gọi mạng |
| Sẵn sàng | Một provider sập (timeout, 5xx, 429 kéo dài, key hỏng) thì run vẫn hoàn tất bằng provider khác trong ngân sách thời gian của run (60 s) |
| Đúng đắn | Fallback **không được âm thầm hạ chất lượng**: cùng policy, cùng tool, output qua cùng bộ kiểm tra (`PROJECT_CONTEXT` §19.7) |
| Bảo trì | Thêm/đổi provider = một file adapter + vài dòng cấu hình; đổi thứ tự ưu tiên không cần sửa code |
| Quan sát | Biết mỗi run do provider/model nào phục vụ, failover vì lý do gì, chi phí bao nhiêu |
| Bảo mật | Key chỉ ở agent worker; mỗi provider một biến môi trường; tắt khẩn cấp một provider không cần deploy |

## Decision

### 1. Định dạng trung lập ở giữa (canonical)

Runtime chỉ làm việc với kiểu dữ liệu của mình: `LLMRequest`, `LLMResult`, `Turn` (user / assistant / tool_result), `ToolCall`, `Usage`. **Adapter** dịch sang và từ định dạng của provider. Lịch sử một run lưu ở dạng trung lập ⇒ có thể phát lại sang provider khác bất cứ lúc nào.

### 2. Adapter mỏng, dùng SDK chính thức

| Adapter | SDK | Ghi chú dịch |
|---|---|---|
| `AnthropicAdapter` | `anthropic` (async), `base_url` + key lấy từ cấu hình ⇒ trỏ được vào gateway | tool `strict`, `output_config.format`, `cache_control` cho prefix ổn định, `effort`; thinking không tắt được trên Opus 5.5 |
| `OpenAIAdapter` | `openai` (async) | function tool `strict`, structured output bằng JSON schema; prompt caching của OpenAI là tự động theo prefix |
| `FakeLLM` | — | kịch bản cho test; cũng dùng để mô phỏng lỗi khi test failover |

SDK đặt `max_retries=0`: mọi retry/failover do **một chỗ** quyết định (mục 4), để không cộng dồn thời gian chờ ẩn.

### 3. Route: tên logic → danh sách đích theo thứ tự

Template agent không ghi tên model của provider nữa mà ghi **route** (`classify`, `draft`). File `agent_runtime/llm/routes.toml` (trong repo, review qua PR):

```toml
[routes.classify]
targets = [
  { provider = "anthropic", model = "claude-sonnet-5", effort = "low", max_tokens = 1024 },
  { provider = "openai",    model = "gpt-4.1-mini",    max_tokens = 512 },
]

[routes.draft]
targets = [
  { provider = "anthropic", model = "claude-sonnet-5", effort = "medium", max_tokens = 4096 },
  { provider = "openai",    model = "gpt-4.1",         max_tokens = 2048 },
]
```

- Provider không có key ⇒ bị loại khỏi mọi route lúc khởi động (log cảnh báo).
- `LLM_DISABLED_PROVIDERS=anthropic` ⇒ tắt khẩn cấp một provider, không cần sửa file hay deploy.
- **Một đích chỉ được vào route sau khi qua eval suite** (bước 2.12) — nếu không, fallback là hạ chất lượng âm thầm.

### 4. `FailoverLLM`: một `LLMPort` bọc nhiều adapter

```text
cho từng đích trong route (đã lọc provider tắt / thiếu capability / circuit đang mở):
  nếu không còn đủ thời gian của run ⇒ dừng, báo lỗi
  gọi adapter với timeout = min(timeout đích, thời gian còn lại)
  thành công ⇒ ghi served_by, đóng circuit, trả kết quả
  lỗi "provider có vấn đề" ⇒ ghi failover(from, to, reason), mở circuit nếu đủ ngưỡng, thử đích kế
  lỗi "do chính yêu cầu" ⇒ KHÔNG thử đích khác, trả lỗi ngay
```

| Lỗi | Phân loại | Hành động |
|---|---|---|
| timeout, lỗi kết nối, HTTP 5xx, 529 overloaded | provider có vấn đề | failover |
| 429 rate limit | provider có vấn đề | failover (không chờ `retry-after` dài trong run 60 s) |
| 401/403 (key hỏng, hết hạn) | provider có vấn đề + **cảnh báo** | failover; circuit mở lâu |
| 400 yêu cầu sai, vượt context | do yêu cầu | không failover (đích khác cũng sẽ lỗi) — là bug cần sửa |
| `refusal` (từ chối vì an toàn) | do nội dung | **không** failover sang provider khác; run ⇒ gợi ý chuyển người (§19.7: không "lỗi là gọi model khác rồi gửi") |
| output sai schema | do model | thử lại **một lần** trên cùng đích, rồi failover |

**Circuit breaker theo provider** (đóng → mở → nửa mở): 3 lỗi "provider có vấn đề" liên tiếp ⇒ mở 30 s (key hỏng: 10 phút); hết hạn cho đúng một yêu cầu thật đi qua để thử. Không ping chủ động (tốn tiền). `ponytail:` trạng thái giữ trong từng process; chia sẻ qua Redis khi chạy nhiều worker và đo thấy cần.

### 5. Một run dính một provider ở từng bước

Vòng tool của bước soạn nháp chạy trọn trên **một** provider (định dạng tool call và thinking block khác nhau giữa provider; thinking block của Claude chỉ hợp lệ với chính model đã sinh ra nó). Provider lỗi giữa vòng ⇒ **bắt đầu lại bước đó** trên đích kế, từ lịch sử trung lập (tin khách + kết quả tool đã có), không ghép nửa vòng của provider này với nửa vòng của provider kia.

### 6. Usage và chi phí chuẩn hóa

`Usage(input, cache_read, cache_write, output)` cho mọi provider; `pricing.py` có bảng giá theo `(provider, model)` kèm `price_version`. `agent_runs` và `usage_events` lưu `provider`, `model`, `failover_from`. Metric: `llm_requests_total{provider,model,outcome}`, `llm_failover_total{from,to,reason}`, `llm_circuit_open{provider}`.

### 7. Kiểm thử

- **Contract test chung cho mọi adapter**: cùng bộ ca (văn bản, tool call, structured output, lỗi) chạy trên từng adapter với HTTP được ghi sẵn — adapter mới phải qua đúng bộ này.
- **Failover test bằng `FakeLLM`**: provider 1 timeout ⇒ provider 2 phục vụ; refusal ⇒ không failover; circuit mở ⇒ bỏ qua đích; hết thời gian run ⇒ dừng.
- `pytest -m live` (tùy chọn, cần key thật) gọi mỗi provider một lần — không chạy trong CI.

## Alternatives considered

- **LiteLLM / thư viện hợp nhất nhiều provider**: nhanh có failover, nhưng là lớp trừu tượng dày, che các tính năng riêng (cache_control, effort, thinking), thêm phụ thuộc lớn; đi ngược `PROJECT_CONTEXT` §3.4.1 (phải thấy prompt và token thật).
- **Một gateway ngoài (OpenRouter, UniversalAI) làm failover**: gateway thành điểm sập duy nhất và đọc toàn bộ dữ liệu; chính UniversalAI đã chèn token ẩn.
- **Server-side `fallbacks` của Claude API**: chỉ fallback giữa các model Claude, chủ yếu cho refusal; không giúp khi đường tới Claude sập; gateway có thể không hỗ trợ.
- **Hedging (gọi song song hai provider, lấy kết quả nhanh hơn)**: giảm độ trễ đuôi nhưng nhân đôi chi phí; chưa cần.
- **Một provider duy nhất**: đơn giản nhất nhưng không đạt yêu cầu sẵn sàng (đặc biệt khi đường Claude đi qua gateway bên thứ ba).

## Consequences

### Positive
- Một provider sập không làm agent ngừng; đổi thứ tự hay tắt provider bằng cấu hình.
- Thêm provider mới là việc cục bộ: adapter + bảng giá + dòng route + qua contract test.
- Mọi run truy được do ai phục vụ và tốn bao nhiêu; so sánh chất lượng giữa provider bằng eval.

### Negative
- Phải duy trì hai adapter và lớp dịch định dạng; tính năng riêng của một provider chỉ dùng được nếu có cách tương đương hoặc được đánh dấu tùy chọn.
- Câu trả lời khác nhau giữa provider (giọng văn, độ dài) ⇒ eval phải chạy trên **từng** đích, và báo cáo phải tách theo `served_by`.
- Prompt cache không dùng chung giữa provider: lần failover đầu tiên đắt hơn.
- Circuit breaker theo process: N worker cần N lần lỗi để cùng nhận ra một provider sập.

### Neutral
- Mặc định ban đầu: **Claude Sonnet 5, effort `medium`** cho soạn nháp (phân loại dùng `low` vì chỉ chọn nhãn; đổi một dòng trong `routes.toml` nếu muốn `medium` cả hai) là đích chính; OpenAI `gpt-4.1` / `gpt-4.1-mini` là dự phòng. Sonnet 5 rẻ hơn Opus 5.5 (giá niêm yết $2 / $10 so với $4 / $20 mỗi triệu token vào/ra). Thứ tự thật chốt bằng số liệu eval ở bước 2.12.
- Key mỗi môi trường một bộ (dev/test dùng gateway, production dùng API chính thức); xoay key không cần deploy lại code.

## Conditions to revisit

- Gateway chèn token làm sai lệch chi phí/eval đáng kể ⇒ chuyển Claude sang API chính thức hoặc đảo thứ tự ưu tiên.
- Nhiều worker và failover chậm nhận ra sự cố ⇒ trạng thái circuit chia sẻ qua Redis.
- Cần stream câu trả lời trực tiếp cho người dùng (Autopilot) ⇒ bổ sung `stream()` vào port; failover giữa chừng stream cần thiết kế riêng.
- Khách hàng thật yêu cầu khu vực xử lý dữ liệu ⇒ ràng buộc route theo tenant (ADR-0027).
