# Báo cáo tải khói bước 1 — 2026-11-20

**Đây là kiểm tra khói, không phải benchmark.** Mục đích: xác nhận đường inbound + outbox chạy ổn dưới tải song song nhỏ và có số liệu nền để so sánh về sau. Không suy ra năng lực production từ số này (§22.3).

## Môi trường

| Mục | Giá trị |
|---|---|
| Máy | Windows 11 Pro (10.0.26300), Intel x86-64, 24 luồng CPU |
| Postgres | `pgvector/pgvector:pg17` trong Docker Desktop 29.7.2 (cổng 54329), cấu hình mặc định |
| Redis | `redis:7` trong Docker Desktop, cấu hình mặc định |
| API | `uvicorn app.entrypoints.api:app`, 1 process, không `--workers`, chạy trực tiếp trên Windows (không Docker) |
| Relay | 1 process `app.entrypoints.worker`, batch 100, poll 200 ms |
| Client | `backend/scripts/smoke_load.py` trên cùng máy, httpx async |
| Rate limit | bật như production (giữ tải dưới 120 tin/phút/IP) |

## Kịch bản

6 visitor song song, mỗi visitor mở session rồi gửi tuần tự 18 tin (tổng 108 tin) vào cùng một connection; mỗi visitor là một conversation riêng.

```powershell
cd backend
uv run python scripts/smoke_load.py --key <public_key> --visitors 6 --messages 18
```

## Kết quả

| Chỉ số | p50 | p95 | max |
|---|---|---|---|
| `POST /public/webchat/v1/messages` (ms, phía client) | 141.7 | 228.0 | 235.5 |
| outbox → publish, `published_at − created_at` (ms) | 132.2 | 245.0 | 277.8 |

- 108/108 tin `201`, không lỗi, không trùng; `inbound_messages_total{result="created"} = 108`.
- Sau khi chạy: `outbox_unpublished_count = 0`, `outbox_oldest_age_seconds = 0`.
- Log access JSON không chứa nội dung tin, token, IP (đã kiểm tra trên log thật).

## Nhận xét

- Độ trễ outbox → publish (~130 ms p50) phù hợp với poll 200 ms (trung bình chờ ~100 ms + publish). Nâng cấp khi cần: `pg_notify` đánh thức relay (ADR-0013).
- Latency POST gồm cả hai round-trip phụ (fast-path đọc idempotency + transaction ghi) trên Postgres trong Docker Desktop/WSL2 — môi trường này chậm hơn đáng kể so với Postgres chạy native; không dùng làm con số mục tiêu.

## Lần sau

Đo trên môi trường gần production (Linux, Postgres native), tăng tải bằng cách nới rate limit cho môi trường thử, ghi thêm `http_request_duration_seconds` từ `/metrics`.
