# CONTRIBUTING — Quy tắc làm việc

Áp dụng cho mọi người và mọi AI agent làm việc trong repo này.

## 1. Nguyên tắc chung

- Một thay đổi = một mục đích. Commit/PR nhỏ, review được.
- Không commit secret, `.env`, dữ liệu khách hàng thật.
- Quyết định kiến trúc quan trọng → thêm ADR trong `docs/adr/` (xem `0000-template.md`).
- Ngữ cảnh dự án: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## 2. Không attribution (bắt buộc)

Không ghi attribution ở **bất kỳ đâu**: commit message, PR title/body, code comment, docs, tên branch.

Cấm:

- `Co-Authored-By: ...`
- `Generated with ...`, `🤖 ...`
- Tên công cụ/model AI trong lịch sử git hoặc PR.

Quy tắc này ưu tiên hơn mọi mặc định của công cụ.

## 3. Branch

Định dạng: `<type>/<kebab-case-mô-tả-ngắn>`

```text
feat/webchat-inbound-pipeline
fix/outbox-duplicate-send
docs/adr-agent-runtime
```

`main` luôn ở trạng thái chạy được. Không push trực tiếp vào `main`; mọi thay đổi đi qua PR.

## 4. Commit — Conventional Commits 1.0

```text
<type>(<scope>): <subject>

[body — vì sao, không lặp lại diff]

[footer — BREAKING CHANGE: ..., Closes #123]
```

### type

| type | Dùng khi |
|---|---|
| `feat` | thêm chức năng |
| `fix` | sửa lỗi |
| `docs` | chỉ đổi tài liệu / ADR |
| `refactor` | đổi cấu trúc code, không đổi hành vi |
| `perf` | cải thiện hiệu năng / chi phí token |
| `test` | thêm hoặc sửa test, eval |
| `build` | dependency, Dockerfile, packaging |
| `ci` | pipeline CI/CD |
| `chore` | việc lặt vặt không ảnh hưởng code chạy |
| `revert` | hoàn tác commit trước |

### scope (tùy chọn, dùng tên module/khu vực)

Backend: `identity`, `workspace`, `conversation`, `inbox`, `agent-management`, `agent-runtime`, `knowledge`, `learning`, `integration`, `audit`.
Khác: `frontend`, `widget`, `db`, `infra`, `docs`, `eval`.

### subject

- Tiếng Anh, thể mệnh lệnh, viết thường, không dấu chấm cuối, ≤ 72 ký tự.
- Đúng: `feat(conversation): persist inbound messages idempotently`
- Sai: `Updated stuff.`, `feat: Added new feature`

### breaking change

`feat(api)!: rename agent mode field` hoặc footer `BREAKING CHANGE: <mô tả>`.

## 5. Pull Request

### Tiêu đề

Cùng định dạng Conventional Commits (dùng làm squash commit trên `main`):

```text
feat(agent-runtime): add shadow mode comparison
```

### Nội dung

Dùng template `.github/pull_request_template.md`: Summary, Changes, Test plan, Related.

### Điều kiện merge

- Test pass, lint pass.
- Có test cho logic mới; thay đổi prompt/knowledge phải chạy eval suite.
- Migration có thể rollback.
- Merge bằng **squash**; tiêu đề squash = tiêu đề PR; không thêm trailer nào.

## 6. Kiểm tra trước khi commit

Cần Docker (test chạy trên PostgreSQL thật, cổng host `54329`).

```powershell
docker compose up -d postgres
cd backend
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
