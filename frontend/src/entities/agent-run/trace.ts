// Logic thuần của trace view: nhãn, chi phí, token. Tách khỏi React để test được.

export type RunStatus =
  | "running"
  | "completed"
  | "handoff_suggested"
  | "skipped"
  | "superseded"
  | "failed"
  | "timed_out";

export type RunListItem = {
  id: string;
  conversation_id: string;
  status: RunStatus;
  skip_reason: string | null;
  error_code: string | null;
  intents: string[];
  model_calls: number;
  tool_calls: number;
  cost_micro: number;
  started_at: string;
  finished_at: string | null;
  grounded: boolean | null;
  needs_human: boolean | null;
};

export type Totals = {
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  cost_micro: number;
};

export type Step = {
  index: number;
  kind: "rule" | "classify" | "llm" | "tool" | "validate";
  name: string | null;
  status: string;
  latency_ms: number | null;
  detail: Record<string, unknown>;
};

export type Source = { type: "faq" | "doc" | "product"; id: string };

export type RunDetail = {
  id: string;
  conversation_id: string;
  status: RunStatus;
  skip_reason: string | null;
  error_code: string | null;
  intents: string[];
  prompt_version: string;
  knowledge_hash: string;
  agent_version: { id: string; number: number };
  started_at: string;
  finished_at: string | null;
  totals: Totals;
  input_messages: { sequence: number; from: string; content: string }[];
  steps: Step[];
  draft: {
    id: string;
    text: string;
    sources: Source[];
    grounded: boolean;
    needs_human: boolean;
    reason: string | null;
  } | null;
  usage: {
    route: string;
    provider: string;
    model: string;
    input_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    output_tokens: number;
    cost_micro: number;
    failed_over_from: string | null;
  }[];
};

const STATUS: Record<RunStatus, string> = {
  running: "Đang chạy",
  completed: "Có nháp",
  handoff_suggested: "Nên chuyển người",
  skipped: "Bỏ qua",
  superseded: "Có tin mới hơn",
  failed: "Lỗi",
  timed_out: "Quá thời gian",
};

const REASON: Record<string, string> = {
  kill_switch: "AI đang tắt",
  mode_manual: "Agent ở chế độ Manual",
  mode_changed: "Đổi mode giữa chừng",
  daily_cap: "Hết trần chi phí ngày",
  sampled_out: "Không nằm trong mẫu",
  no_customer_message: "Không có tin khách",
  budget_exceeded: "Vượt ngân sách run",
  llm_unavailable: "Nhà cung cấp AI không phản hồi",
  empty_reply: "Model trả nháp rỗng",
  max_tokens: "Hết giới hạn token",
  wall_clock: "Quá thời gian chạy",
};

export function statusLabel(status: RunStatus): string {
  return STATUS[status] ?? status;
}

/** Lý do con người đọc được cho run không có nháp (hoặc lỗi). */
export function reasonLabel(code: string | null): string | null {
  return code ? (REASON[code] ?? code) : null;
}

/** micro-USD → "$0.0123". Chi phí dương nhưng rất nhỏ vẫn hiện "<$0.0001", không làm tròn về 0. */
export function formatCost(micro: number): string {
  if (micro <= 0) return "$0";
  const usd = micro / 1_000_000;
  return usd < 0.0001 ? "<$0.0001" : `$${usd.toFixed(4)}`;
}

/** Tỉ lệ token vào được phục vụ từ cache (0..1); null nếu chưa có token vào. */
export function cacheHitRate(t: Pick<Totals, "input_tokens" | "cache_read_tokens" | "cache_write_tokens">): number | null {
  const total = t.input_tokens + t.cache_read_tokens + t.cache_write_tokens;
  return total === 0 ? null : t.cache_read_tokens / total;
}

export function tokenRows(t: Totals): { label: string; value: number }[] {
  return [
    { label: "Vào (thường)", value: t.input_tokens },
    { label: "Cache đọc", value: t.cache_read_tokens },
    { label: "Cache ghi", value: t.cache_write_tokens },
    { label: "Ra", value: t.output_tokens },
  ];
}

const KIND: Record<Step["kind"], string> = {
  rule: "Rule",
  classify: "Phân loại",
  llm: "Soạn nháp",
  tool: "Tool",
  validate: "Kiểm tra",
};

export function stepTitle(s: Step): string {
  return s.name ? `${KIND[s.kind]} · ${s.name}` : KIND[s.kind];
}

/** Tóm tắt một dòng cho step, chỉ từ phần đã chiếu (backend không lưu lời khách hay prompt). */
export function stepSummary(s: Step): string {
  const d = s.detail;
  if (s.kind === "tool") {
    const keys = Array.isArray(d.arg_keys) ? (d.arg_keys as string[]).join(", ") : "";
    return `tham số: ${keys || "—"} · ${d.records ?? 0} kết quả`;
  }
  if (s.kind === "classify" || s.kind === "llm") {
    const tokens = d.tokens as { in: number; cache_read: number; out: number } | undefined;
    const failover = Array.isArray(d.failover_from) && d.failover_from.length > 0;
    return [
      `${d.provider}:${d.model}`,
      tokens ? `${tokens.in} vào / ${tokens.cache_read} cache / ${tokens.out} ra` : "",
      failover ? `chuyển nhà cung cấp từ ${(d.failover_from as string[]).join(", ")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (s.kind === "validate") {
    const problems = Array.isArray(d.problems) ? (d.problems as string[]) : [];
    return problems.length === 0 ? "hợp lệ" : `vấn đề: ${problems.join(", ")}`;
  }
  return s.status;
}
