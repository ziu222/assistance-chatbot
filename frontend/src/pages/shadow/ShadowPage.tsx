import { LABELS, useCalibration, useComparisons, useLabel, type Label } from "../../entities/shadow/api";

/** Chủ shop gán nhãn cặp nháp ↔ câu nhân viên để hiệu chỉnh giám khảo (thiết kế 04 §6.3). UI thử nghiệm. */
export function ShadowPage() {
  const cal = useCalibration();
  const list = useComparisons();
  const label = useLabel();
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
