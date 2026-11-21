# Hướng dẫn tự test lát cắt bước 1

Tài liệu đi kèm [thiết kế bước 1](../design/01-webchat-inbound-pipeline.md). Phần A review nhóm 1 (mục 1–3: phạm vi, quyết định, kiến trúc) so với code đã merge. Phần B là kế hoạch test chi tiết cho nhóm 2 (mục 4–10: cách hệ thống được xây), mỗi mục có: cần chứng minh điều gì, lệnh chạy, kết quả mong đợi, và test tự động tương ứng.

Mọi lệnh viết cho **PowerShell 5.1** trên Windows và đã được chạy thử trên máy dev.

---

## Phần A — Review nhóm 1 (mục 1–3)

### A.1. Phạm vi (mục 1.2) đã làm đủ chưa

| # | Hạng mục | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | Widget loader + iframe, vi/en, responsive, a11y | Xong | PR #8 |
| 2 | Session token ký, origin, rate limit | Xong, **có điều chỉnh** | Origin: xem A.3 |
| 3 | Channel adapter `webchat` + `NormalizedChannelEvent` | **Một phần** | Có `NormalizedChannelEvent`; chưa có lớp `ChannelAdapter` riêng — xác thực/parse nằm thẳng trong router `public_webchat.py` |
| 4 | `ReceiveInboundMessage` idempotent + `sequence` | Xong | PR #3 |
| 5 | Resolve Customer / Identity / Conversation | Xong | |
| 6 | Outbox + relay | Xong | PR #4 |
| 7 | SSE cho nhân viên và khách | Xong | PR #7 |
| 8 | Inbox: danh sách, timeline, trả lời, đã đọc | Xong | PR #6 (API), #9 (UI) |
| 9 | Auth nhân viên tối thiểu | Xong | |
| 10 | Seed CLI, observability, test, ADR | Xong | lệnh là `seed`, không phải `seed-demo` như §13.3 ghi |

### A.2. Quyết định D1–D11 (mục 2)

Tất cả được giữ đúng trong code. Khác biệt nhỏ:

- **D2**: `kid` nằm ở **header** JWT (chuẩn), không nằm trong payload như ví dụ ở §7.1.
- **D7**: tín hiệu SSE chưa mang `trace_id` (thiết kế §13.1 nói "chuyển tiếp vào signal SSE").

### A.3. Kiến trúc (mục 3) — chỗ thiết kế sai hoặc lệch

| Chỗ | Thiết kế ghi | Thực tế | Mức |
|---|---|---|---|
| §3.2 bước "verify token, **origin**" khi gửi tin | API kiểm Origin theo allowlist | Không thể: request từ iframe mang origin của **iframe**. Allowlist ép bằng `frame-ancestors`; API chỉ nhận `Origin = PUBLIC_ORIGIN` ở `sessions` (ADR-0017) | Thiết kế sai; đã sửa ở §3.2, §7.1, §8.2 |
| §3.1 "API chạy được nhiều instance" | — | Đúng, nhưng giới hạn SSE (3/visitor, 20/tenant) đếm **theo process** | Giới hạn đã ghi chú |
| §1.1 "bước 2 gắn vào outbox `message.created`" | — | Relay hiện publish lên Redis pub/sub (**at-most-once**). Agent ở bước 2 **không** được dùng kênh này; cần consumer bền (ADR-0008) trước khi làm bước 2 | **Quan trọng cho bước 2** |
| §4.3 "module không đọc bảng của module khác" | — | Read model inbox (`inbox/infrastructure/queries.py`) JOIN thẳng `customers`, `channel_connections`; router public gọi repository của `conversation`/`customer` | Nợ kiến trúc nhỏ, chấp nhận ở bước 1 |
| §13.3 docker-compose có service `api`, `relay` | — | Chưa có (chưa có Dockerfile) | Chưa làm |

### A.4. Kết luận review

Nhóm 1 đứng vững: mục tiêu ở §1.1 đạt và có bằng chứng (E2E + test đồng thời). Hai việc nên làm trước bước 2: (1) chọn consumer bền cho outbox (ADR-0008) — bắt buộc, (2) tách `ChannelAdapter` khi thêm kênh thứ hai.

---

## Phần B — Kế hoạch test nhóm 2 (mục 4–10)

### B.0. Chuẩn bị (một lần)

**Terminal 1 — hạ tầng + DB + tenant mẫu:**

```powershell
cd D:\atlas
docker compose up -d postgres redis
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.entrypoints.cli seed --tenant "Shop Test" --email tester@shop.test --origin http://localhost:5500
```

Ghi lại `public_key` và `password` (mật khẩu chỉ hiện một lần). Email đã dùng thì đổi email khác.

**Tạo secret ký token** — chạy trong Terminal 2 trước khi bật API (biến môi trường chỉ sống trong terminal đó):

```powershell
$b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $env:WEBCHAT_TOKEN_SECRETS = '{"k1": "' + [Convert]::ToBase64String($b) + '"}'
```

**Terminal 2 — API**, **Terminal 3 — relay worker**:

```powershell
cd D:\atlas\backend; uv run uvicorn app.entrypoints.api:app --port 8000
```

```powershell
cd D:\atlas\backend; uv run python -m app.entrypoints.worker
```

**Terminal 4 — widget + trang mẫu**, **Terminal 5 — admin inbox**:

```powershell
cd D:\atlas\widget; npm ci; npm run build; cd ..\backend; uv run python -m http.server 5500 -d ..\widget\demo
```

```powershell
cd D:\atlas\frontend; npm ci; npm run dev
```

**Helper gọi API** — dán một lần vào terminal dùng để test (Terminal 6). Nó trả `Status` và `Body` kể cả khi lỗi, giải mã UTF-8 đúng tiếng Việt:

```powershell
$Api = "http://localhost:8000"
function Call {
  param([string]$Method, [string]$Path, $Body = $null, [hashtable]$Headers = @{})
  $p = @{ Method = $Method; Uri = "$Api$Path"; Headers = $Headers; UseBasicParsing = $true }
  if ($null -ne $Body) {
    $p.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress))
    $p.ContentType = "application/json; charset=utf-8"
  }
  try {
    $r = Invoke-WebRequest @p
    $status = [int]$r.StatusCode
    $text = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
  } catch {
    if (-not $_.Exception.Response) { throw }
    $status = [int]$_.Exception.Response.StatusCode
    $text = $_.ErrorDetails.Message
  }
  [pscustomobject]@{ Status = $status; Body = $(if ($text) { $text | ConvertFrom-Json } else { $null }) }
}
$Key = "wpk_..."   # public_key từ seed
function Sql($q) { docker compose -f D:\atlas\docker-compose.yml exec -T postgres psql -U atlas -d atlas -c $q }
```

Mở một phiên khách (dùng cho các mục sau):

```powershell
$s = Call POST /public/webchat/v1/sessions @{ public_key = $Key } @{ Origin = "http://localhost:8000" }
$Token = $s.Body.token
$Auth = @{ Authorization = "Bearer $Token" }
$s.Status   # 200
```

---

### B.4. Mô hình miền — 7 bất biến (mục 4.2)

| Bất biến | Cách test tay | Mong đợi |
|---|---|---|
| 2. Idempotency | Gửi 2 lần cùng `client_message_id` (lệnh dưới) | Lần 1 `201`, lần 2 `200`, cùng `message_id`; DB chỉ 1 dòng |
| 3. `sequence` liên tục | Gửi 3 tin, xem `sequence` | 1, 2, 3 không gap |
| 4. Một conversation `open` | Câu SQL dưới | 0 dòng |
| 5. Đã `closed` thì tin mới mở conversation mới | Đóng bằng SQL rồi gửi tiếp | `conversation_id` mới, `sequence` về 1 |
| 6. Không gửi vào `closed` | Trả lời từ admin vào hội thoại đã đóng | Ô nhập bị khóa; API trả `409 conversation_closed` |

```powershell
$Cmid = [guid]::NewGuid().ToString()
$a = Call POST /public/webchat/v1/messages @{ client_message_id = $Cmid; text = "Đơn #123 đâu rồi?" } $Auth
$b = Call POST /public/webchat/v1/messages @{ client_message_id = $Cmid; text = "gửi lại" } $Auth
"$($a.Status) $($b.Status) cùng-id=$($a.Body.message_id -eq $b.Body.message_id) seq=$($a.Body.sequence)"
$Conv = $a.Body.conversation_id

Sql "SELECT tenant_id, customer_id, channel_connection_id, count(*) FROM conversations WHERE status='open' GROUP BY 1,2,3 HAVING count(*) > 1;"

# Bất biến 5: đóng rồi nhắn tiếp
Sql "UPDATE conversations SET status='closed', closed_at=now() WHERE id='$Conv';"
$c = Call POST /public/webchat/v1/messages @{ client_message_id = [guid]::NewGuid().ToString(); text = "Tôi hỏi tiếp" } $Auth
"conversation mới: $($c.Body.conversation_id -ne $Conv), seq=$($c.Body.sequence)"
```

Bất biến dưới **đồng thời** (thứ tay không làm được) có test tự động trên Postgres thật:

```powershell
cd D:\atlas\backend; uv run pytest -v tests/integration/test_receive_inbound_message.py
```

Đọc tên test: 50 request cùng id ⇒ 1 tin; 100 tin song song ⇒ sequence 1..100; 2 tin đầu đồng thời ⇒ 1 customer/1 conversation; ép lỗi giữa chừng ⇒ không dòng mồ côi; 12 conversation liên tiếp (hồi quy lỗi generic plan).

---

### B.5. Dữ liệu (mục 5)

| Cần chứng minh | Lệnh | Mong đợi |
|---|---|---|
| Tin lưu đúng nguyên văn, có `trace_id` | `Sql "SELECT sequence, direction, sender_type, content, trace_id FROM messages ORDER BY created_at DESC LIMIT 5;"` | Tiếng Việt đúng, `trace_id` có giá trị |
| Mở widget không tạo rác DB (D10) | Đếm `customers` → gọi `sessions` 5 lần → đếm lại | Số dòng không đổi |
| DB chặn tham chiếu chéo tenant | Test tự động bên dưới | IntegrityError |
| Migration lên/xuống được | `uv run alembic downgrade base; uv run alembic upgrade head` (chỉ trên DB thử!) | Không lỗi |

```powershell
Sql "SELECT count(*) FROM customers;"
1..5 | ForEach-Object { (Call POST /public/webchat/v1/sessions @{ public_key = $Key } @{ Origin = "http://localhost:8000" }).Status }
Sql "SELECT count(*) FROM customers;"
```

```powershell
cd D:\atlas\backend; uv run pytest -v tests/integration/test_schema_constraints.py tests/integration/test_migrations.py tests/integration/test_schema_drift.py
```

---

### B.6. Hợp đồng nội bộ — outbox chỉ mang ID (mục 6.3)

```powershell
Sql "SELECT id, event_type, payload->>'sequence' AS seq, payload ? 'text' AS co_text, payload ? 'content' AS co_content, published_at IS NOT NULL AS da_publish FROM outbox_events ORDER BY id DESC LIMIT 5;"
```

Mong đợi: `co_text`, `co_content` đều `f`; mỗi tin có `message.created`, conversation mới có thêm `conversation.created`; `da_publish = t` khi worker đang chạy.

---

### B.7. Giao diện API (mục 7)

**Public — đường thành công và đường lỗi:**

| Kịch bản | Lệnh | Mong đợi |
|---|---|---|
| Lịch sử của khách | `(Call GET "/public/webchat/v1/messages?after_sequence=0" $null $Auth).Body.messages` | Danh sách tin, theo `sequence` |
| Gia hạn token giữ lịch sử | `Call POST /public/webchat/v1/sessions @{ public_key = $Key; token = $Token } @{ Origin = "http://localhost:8000" }` rồi đọc lịch sử bằng token mới | Cùng lịch sử |
| Origin lạ | `Call POST /public/webchat/v1/sessions @{ public_key = $Key } @{ Origin = "https://evil.vn" }` | `403 origin_not_allowed` |
| Token giả | `Call GET /public/webchat/v1/messages $null @{ Authorization = "Bearer fake" }` | `401 invalid_token` |
| Tự khai tenant | `Call POST /public/webchat/v1/messages @{ client_message_id = [guid]::NewGuid().ToString(); text = "x"; tenant_id = "hack" } $Auth` | `422` |
| id không phải UUID v4 | `Call POST /public/webchat/v1/messages @{ client_message_id = "abc"; text = "x" } $Auth` | `422 invalid_request` |
| Rate limit | `1..21 \| % { (Call POST /public/webchat/v1/messages @{ client_message_id = [guid]::NewGuid().ToString(); text = "spam" } $Auth).Status }` | 20 lần `201`, lần 21 `429` |

Xem `.Body.error.code` và header `X-Trace-Id` trong mọi lỗi.

**SSE — nhìn tín hiệu tận mắt.** Mở Terminal 7 (cần `$Api` và `$Token` giống Terminal 6):

```powershell
curl.exe -N -H "Authorization: Bearer $Token" "$Api/public/webchat/v1/events"
```

Gửi một tin ở Terminal 6 (hoặc trả lời từ admin). Terminal 7 hiện:

```text
event: signal
id: 213
data: {"t":"message.created","c":"<conversation_id>","s":1}
```

Chỉ có ID và `sequence`, không có nội dung. Để yên 15 giây sẽ thấy `: heartbeat`. Ctrl+C để dừng.

**API nhân viên** dùng cookie `__Host-sid` (Secure), test qua UI ở B.10 hoặc test tự động:

```powershell
cd D:\atlas\backend; uv run pytest -v tests/integration/test_staff_api.py tests/integration/test_public_webchat.py
```

---

### B.8. Backend — độ tin cậy (mục 8)

**Relay chết không mất tin.** Ctrl+C worker ở Terminal 3, rồi:

```powershell
1..3 | ForEach-Object { (Call POST /public/webchat/v1/messages @{ client_message_id = [guid]::NewGuid().ToString(); text = "khi worker tắt $_" } $Auth).Status }
(Invoke-WebRequest -UseBasicParsing "$Api/metrics").Content -split "`n" | Select-String "^outbox_(unpublished_count|oldest_age_seconds)"
```

Mong đợi: 3 lần `201` (khách không bị ảnh hưởng), `outbox_unpublished_count` > 0. Bật lại worker, chạy lại dòng metric sau vài giây ⇒ về `0`. Admin inbox (nếu đang mở) hiện "Đang kết nối lại…" không đổi, tin vẫn tới qua polling/refetch.

**Redis chết.** Mở phiên khách **trước**, rồi:

```powershell
docker compose -f D:\atlas\docker-compose.yml stop redis
(Call POST /public/webchat/v1/sessions @{ public_key = $Key } @{ Origin = "http://localhost:8000" }).Status   # 503: sessions fail closed
(Call POST /public/webchat/v1/messages @{ client_message_id = [guid]::NewGuid().ToString(); text = "Redis chết vẫn nhận" } $Auth).Status   # 201: messages fail open
(Call GET /health/ready).Body   # status=ready, redis=down
docker compose -f D:\atlas\docker-compose.yml start redis
```

Sau khi bật lại, admin inbox tự về "Trực tuyến" (hub kết nối lại và gửi `resync`).

**Log không lộ dữ liệu.** Xem Terminal 2: mỗi request là một dòng JSON có `trace_id`, `route` là template (`/w/{public_key}`), **không** có IP, query string hay nội dung tin.

```powershell
cd D:\atlas\backend; uv run pytest -v tests/integration/test_outbox_relay.py tests/integration/test_sse.py tests/integration/test_observability.py
```

---

### B.9. Widget (mục 9) — checklist tay

Mở `http://localhost:5500/?key=<public_key>`.

- [ ] Nút chat góc phải giữ màu xanh dù trang mẫu có CSS "phá" (nút hồng, font Comic Sans) — Shadow DOM.
- [ ] Mở widget: thấy lời chào; DevTools → Network: `sessions` trả 200, `events` ở trạng thái pending (stream).
- [ ] Gửi `<img src=x onerror=alert(1)>`: hiện nguyên văn, không hộp thoại.
- [ ] Enter gửi, Shift+Enter xuống dòng, Esc đóng widget.
- [ ] Tải lại trang, mở lại: lịch sử còn.
- [ ] Thu nhỏ cửa sổ < 480px: widget toàn màn hình.
- [ ] Đổi ngôn ngữ trình duyệt sang English: giao diện tiếng Anh.
- [ ] Mở `http://127.0.0.1:5500/?key=...` (origin **không** trong allowlist): iframe trắng, Console báo vi phạm `frame-ancestors`.
- [ ] Bàn phím: Tab tới nút chat, Enter mở, Tab qua ô nhập → Gửi → Đóng với viền focus rõ.

### B.10. Admin inbox (mục 10) — checklist tay

Mở `http://localhost:5173`, đăng nhập bằng email/mật khẩu từ seed.

- [ ] Sai mật khẩu và email không tồn tại cho cùng một thông báo; 6 lần sai liên tiếp ⇒ "Thử quá nhiều lần".
- [ ] Đăng nhập xong: góc trên hiện "● Trực tuyến".
- [ ] Khách nhắn từ widget: hội thoại tự hiện ở cột trái với badge số chưa đọc, **không reload**.
- [ ] Bấm vào hội thoại: badge biến mất (đánh dấu đã đọc), URL có `?c=<id>`.
- [ ] Trả lời: tin hiện ngay bên inbox và tự hiện trong widget của khách.
- [ ] Đóng hội thoại bằng SQL (B.4) rồi tải lại: ô nhập bị khóa "Hội thoại đã đóng".
- [ ] Đăng xuất rồi bấm Back: không xem lại được dữ liệu.
- [ ] Hai tenant: seed tenant thứ hai, đăng nhập bằng tài khoản đó ⇒ không thấy hội thoại của tenant đầu.

---

### B.11. Toàn bộ bằng một lệnh

Khi đã quen tay, chạy cả bộ tự động (dừng API/worker/dev server trước — E2E tự dựng lại):

```powershell
cd D:\atlas\backend; uv run pytest
```

```powershell
cd D:\atlas\e2e; npm ci; npx playwright test
```

Bản đồ test tự động ↔ mục thiết kế:

| Mục | File test |
|---|---|
| 4 Mô hình miền | `test_receive_inbound_message.py`, `test_repositories.py` |
| 5 Dữ liệu | `test_schema_constraints.py`, `test_migrations.py`, `test_schema_drift.py` |
| 6 Hợp đồng | `test_receive_inbound_message.py` (payload outbox), `test_outbox_signal.py` |
| 7 API | `test_public_webchat.py`, `test_staff_api.py`, `test_webchat_token.py`, `test_webchat_origin.py` |
| 8 Backend | `test_outbox_relay.py`, `test_sse.py`, `test_observability.py`, `test_ready.py` |
| 9 Widget | `widget/src/app/*.test.ts`, `e2e/tests/webchat.spec.ts` |
| 10 Inbox | `frontend/src/**/*.test.ts`, `e2e/tests/webchat.spec.ts` |
