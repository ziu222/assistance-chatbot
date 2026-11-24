import { LABELS, useCalibration, useComparisons, useLabel, useShadowSummary, type Label } from "../../entities/shadow/api";

const pct = (x: number | null) => (x === null ? "—" : `${Math.round(x * 100)}%`);

/** Chủ shop gán nhãn cặp nháp ↔ câu nhân viên để hiệu chỉnh giám khảo (thiết kế 04 §6.3). UI thử nghiệm. */
export function ShadowPage() {
  const cal = useCalibration();
  const list = useComparisons();
  const label = useLabel();
  const sum = useShadowSummary(14);
  const c = cal.data;
  return (
    <div className="admin-page">
      <h1>Chấm nháp</h1>
      {c && (
        <p className={c.calibrated ? "" : "muted"} role="status">
          Đã gán {c.labels}/{c.needs.labels} nhãn
          {c.kappa !== null && <> · đồng thuận với giám khảo (kappa) {c.kappa}</>}
          {c.calibrated ? " · giám khảo đã hiệu chỉnh" : " · chưa đủ để tin điểm giám khảo"}
        </p>
      )}
      <h2>Theo ý định (14 ngày, phiên bản cấu hình hiện tại)</h2>
      {sum.data && sum.data.intents.length === 0 && <p className="muted">Chưa có cặp nào.</p>}
      {sum.data && sum.data.intents.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Ý định</th>
              <th>Được chấm</th>
              <th>Gửi được</th>
              <th>Sai</th>
              <th>Chưa ai trả lời</th>
              <th>Bot nhanh hơn</th>
              <th>Tự trả lời</th>
            </tr>
          </thead>
          <tbody>
            {sum.data.intents.map((i) => (
              <tr key={i.intent ?? "-"}>
                <td>{i.intent ?? "—"}</td>
                <td>
                  {i.scored}/{i.total}
                </td>
                <td>{pct(i.sendable_rate)}</td>
                <td>{pct(i.wrong_rate)}</td>
                <td>{i.unanswered}</td>
                <td>{i.median_bot_lead_s === null ? "—" : `${Math.round(i.median_bot_lead_s / 60)} phút`}</td>
                <td title={i.not_ready_because.join("; ")}>{i.ready ? "Đủ điều kiện đề xuất" : i.not_ready_because[0]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Gán nhãn</h2>
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.data && list.data.items.length === 0 && <p className="muted">Chưa có cặp nào chờ gán nhãn.</p>}
      <ul className="plain stack">
        {list.data?.items.map((x) => (
          <li key={x.id}>
            <article className="card" aria-label={`Cặp ${x.intent ?? ""}`}>
              <small className="muted">{x.intent ?? "—"}</small>
              <p className="pre">
                <strong>Khách:</strong> {x.customer}
              </p>
              <p className="pre">
                <strong>Nháp của bot:</strong> {x.draft}
              </p>
              <p className="pre">
                <strong>Nhân viên đã gửi:</strong> {x.staff}
              </p>
              <p className="muted">
                Giám khảo: {LABELS[x.judge.label]}
                {x.judge.rationale && <> — {x.judge.rationale}</>}
              </p>
              <div className="row" role="group" aria-label="Nhãn của bạn">
                {(Object.keys(LABELS) as Label[]).map((l) => (
                  <button key={l} type="button" disabled={label.isPending} onClick={() => label.mutate({ id: x.id, label: l })}>
                    {LABELS[l]}
                  </button>
                ))}
              </div>
            </article>
          </li>
        ))}
      </ul>
    </div>
  );
}
