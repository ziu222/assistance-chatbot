# Eval

| File | Việc |
|---|---|
| `sim_shop/` | Bộ shop giả lập: 12 shop, 490 nhiệm vụ có đáp án (xem `sim_shop/README.md`) |
| `run_eval.py` | Chạy nhiệm vụ qua pipeline thật (tầng chính sách + agent runtime) và viết báo cáo |
| `scoring.py` | Chấm từng chiều riêng (chuyển người, lý do, intent, tool, nguồn, số tiền, điều cấm, tình huống, grounded, an toàn khi thiếu căn cứ) |
| `escalation_demo.json`, `../scripts/eval_escalation.py` | Đo tín hiệu leo thang (bước 2.10) |

```text
# Không gọi mạng (kiểm harness):
uv run python backend/evals/run_eval.py --fake --per-category 2

# Gọi LLM thật bằng key trong backend/.env, có trần chi phí:
uv run python backend/evals/run_eval.py --per-category 4 --max-cost-usd 1 \
    --out docs/evals/agent-eval.md --raw backend/evals/results/run.jsonl
```

Mỗi lần chạy **xóa và dựng lại** DB `<tên DB dev>_eval` (không đụng DB dev). Kết quả từng ca
(`--raw`) ghi vào `backend/evals/results/` (đã gitignore). Báo cáo tách theo nhóm nhiệm vụ, hồ sơ
tài liệu của shop và nguồn catalog; với 4 ca mỗi nhóm, chênh vài điểm giữa hai lần chạy là nhiễu.
Không dùng Message Batches API vì vòng tool của agent là tuần tự.
