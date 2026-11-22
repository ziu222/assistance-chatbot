import { useState } from "react";
import { useRunDetail, useRunList } from "../../entities/agent-run/api";
import {
  cacheHitRate,
  formatCost,
  reasonLabel,
  statusLabel,
  stepSummary,
  stepTitle,
  tokenRows,
  type RunDetail,
  type RunStatus,
  type Source,
} from "../../entities/agent-run/trace";
import { useEntries, useProduct, type DocEntry, type FaqEntry } from "../../entities/knowledge/api";
import { formatTime } from "../../shared/lib/format";

const STATUSES: RunStatus[] = [
  "completed",
  "handoff_suggested",
  "skipped",
  "superseded",
  "failed",
  "timed_out",
  "running",
];

const VND = new Intl.NumberFormat("vi-VN");

/** Mở bản ghi mà nháp trích làm nguồn. FAQ/tài liệu đã lưu trữ vẫn mở được (không bị xóa cứng). */
function SourceView({ source, onClose }: { source: Source; onClose: () => void }) {
  const faqs = useEntries<FaqEntry | DocEntry>("faq");
  const docs = useEntries<FaqEntry | DocEntry>("doc");
  const product = useProduct(source.type === "product" ? source.id : null);

  let body: React.ReactNode = <p className="state">Đang tải…</p>;
  if (source.type === "product") {
    if (product.isError) body = <p className="muted">Không còn bản ghi sản phẩm này.</p>;
    else if (product.data) {
      const p = product.data;
      body = (
        <>
          <strong>{p.name}</strong> <small className="muted">{p.sku}</small>
          <div>{VND.format(p.price_vnd)} đ</div>
          {p.description && <p className="pre">{p.description}</p>}
        </>
      );
    }
  } else {
    const list = source.type === "faq" ? faqs : docs;
    if (list.data) {
      const e = list.data.items.find((x) => x.id === source.id);
      body = !e ? (
        <p className="muted">Không tìm thấy bản ghi.</p>
      ) : "question" in e ? (
        <>
          <strong>{e.question}</strong>
          <p className="pre">{e.answer}</p>
        </>
      ) : (
        <>
          <strong>{e.title}</strong>
          <p className="pre">{e.body}</p>
        </>
      );
    }
  }
  return (
    <aside className="card source-view" aria-label="Nguồn của nháp">
      <div className="row">
        <small className="muted">Nguồn: {source.type}</small>
        <button type="button" className="link" onClick={onClose}>
          Đóng
        </button>
      </div>
      {body}
    </aside>
  );
}

function RunView({ run }: { run: RunDetail }) {
  const [open, setOpen] = useState<Source | null>(null);
  const hit = cacheHitRate(run.totals);
  const why = reasonLabel(run.skip_reason ?? run.error_code);

  return (
    <div className="stack">
      <header>
        <h2>
          <span className={`badge ${run.status}`}>{statusLabel(run.status)}</span>{" "}
          {run.intents.length > 0 && <small className="muted">ý định: {run.intents.join(", ")}</small>}
        </h2>
        {why && <p className="muted">Lý do: {why}</p>}
      </header>

      <section aria-labelledby="t-in">
        <h3 id="t-in">1. Tin khách (đầu vào)</h3>
        <ol className="plain">
          {run.input_messages.map((m) => (
            <li key={m.sequence} className="bubble">
              <small className="muted">#{m.sequence} · {m.from === "customer" ? "khách" : "nhân viên"}</small>
              <div className="pre">{m.content}</div>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="t-steps">
        <h3 id="t-steps">2. Các bước</h3>
        <ol className="steps">
          {run.steps.map((s) => (
            <li key={s.index}>
              <strong>{stepTitle(s)}</strong>
              <span className="muted"> · {s.latency_ms ?? 0} ms</span>
              <div className="muted">{stepSummary(s)}</div>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="t-draft">
        <h3 id="t-draft">3. Bản nháp</h3>
        {run.draft ? (
          <div className="card">
            <p className="pre">{run.draft.text}</p>
            <div className="row">
              <span className={`badge ${run.draft.grounded ? "ok" : "bad"}`}>
                {run.draft.grounded ? "Có căn cứ" : "Không có căn cứ"}
              </span>
              {run.draft.needs_human && <span className="badge handoff_suggested">Cần người</span>}
              {run.draft.reason && <small className="muted">{run.draft.reason}</small>}
            </div>
            {!run.draft.grounded && (
              <p className="error">Nháp này không bao giờ đủ điều kiện để gửi. Chỉ giữ lại để phân tích.</p>
            )}
            {run.draft.sources.length > 0 && (
              <ul className="plain row">
                {run.draft.sources.map((s) => (
                  <li key={`${s.type}:${s.id}`}>
                    <button type="button" className="link" onClick={() => setOpen(s)}>
                      {s.type} · {s.id.slice(0, 8)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {open && <SourceView source={open} onClose={() => setOpen(null)} />}
          </div>
        ) : (
          <p className="muted">Run này không tạo nháp.</p>
        )}
        <p className="muted">Nháp chỉ hiện ở đây, không có trong ô soạn tin và không gửi tới khách.</p>
      </section>

      <section aria-labelledby="t-cost">
        <h3 id="t-cost">4. Token và chi phí</h3>
        <table className="table">
          <thead>
            <tr>
              <th>Lần gọi</th>
              <th>Model</th>
              {tokenRows(run.totals).map((r) => (
                <th key={r.label}>{r.label}</th>
              ))}
              <th>Chi phí</th>
            </tr>
          </thead>
          <tbody>
            {run.usage.map((u, i) => (
              <tr key={i}>
                <td>{u.route}</td>
                <td>
                  {u.provider}:{u.model}
                  {u.failed_over_from && <small className="muted"> (thay {u.failed_over_from})</small>}
                </td>
                <td>{u.input_tokens}</td>
                <td>{u.cache_read_tokens}</td>
                <td>{u.cache_write_tokens}</td>
                <td>{u.output_tokens}</td>
                <td>{formatCost(u.cost_micro)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th colSpan={2}>Tổng</th>
              {tokenRows(run.totals).map((r) => (
                <th key={r.label}>{r.value}</th>
              ))}
              <th>{formatCost(run.totals.cost_micro)}</th>
            </tr>
          </tfoot>
        </table>
        <p className="muted">
          Cache hit: {hit === null ? "—" : `${Math.round(hit * 100)}%`}. Prefix quá ngắn thì không được cache; đó là bình
          thường.
        </p>
      </section>

      <section aria-labelledby="t-lineage">
        <h3 id="t-lineage">5. Truy ngược</h3>
        <dl className="kv">
          <dt>Phiên bản agent</dt>
          <dd>#{run.agent_version.number}</dd>
          <dt>Prompt</dt>
          <dd>{run.prompt_version}</dd>
          <dt>Knowledge hash</dt>
          <dd title={run.knowledge_hash}>{run.knowledge_hash ? run.knowledge_hash.slice(0, 12) : "—"}</dd>
        </dl>
      </section>
    </div>
  );
}

export function TracePage({ selected, onSelect }: { selected: string | null; onSelect: (id: string | null) => void }) {
  const [status, setStatus] = useState<RunStatus | "">("");
  const list = useRunList(status);
  const detail = useRunDetail(selected);

  return (
    <div className="trace">
      <aside className="trace-list">
        <label className="row">
          Trạng thái{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value as RunStatus | "")}>
            <option value="">Tất cả</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </label>
        {list.isPending && <p className="state">Đang tải…</p>}
        {list.isError && (
          <p className="error" role="alert">
            Không tải được danh sách run.
          </p>
        )}
        {list.data && list.runs.length === 0 && <p className="muted">Chưa có run nào.</p>}
        <ul className="plain">
          {list.runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className={`run-item ${r.id === selected ? "active" : ""}`}
                aria-current={r.id === selected}
                onClick={() => onSelect(r.id)}
              >
                <span className={`badge ${r.status}`}>{statusLabel(r.status)}</span>
                <span className="muted"> {formatTime(r.started_at)} · {formatCost(r.cost_micro)}</span>
                <div>
                  {r.intents.join(", ") ||
                    reasonLabel(r.skip_reason ?? r.error_code) ||
                    (r.model_calls === 0 ? "Xử lý bằng rule, không gọi AI" : "—")}
                </div>
              </button>
            </li>
          ))}
        </ul>
        {list.hasNextPage && (
          <button type="button" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            Xem thêm
          </button>
        )}
      </aside>
      <section className="trace-detail">
        {!selected && <p className="state">Chọn một run để xem trace.</p>}
        {selected && detail.isPending && <p className="state">Đang tải…</p>}
        {selected && detail.isError && (
          <p className="error" role="alert">
            Không tải được run này.
          </p>
        )}
        {detail.data && <RunView key={detail.data.id} run={detail.data} />}
      </section>
    </div>
  );
}
