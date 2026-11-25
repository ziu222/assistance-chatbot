import { useState } from "react";
import {
  useAutopilotIntents,
  useDisableAutopilot,
  useEnableAutopilot,
  usePauseAutopilot,
  type AutopilotIntentItem,
} from "../../entities/autopilot/api";
import { LABELS, useCalibration, useComparisons, useLabel, useShadowSummary, type Label } from "../../entities/shadow/api";
import { ApiError } from "../../shared/api/client";

const pct = (x: number | null) => (x === null ? "—" : `${Math.round(x * 100)}%`);

/** Chủ shop gán nhãn cặp nháp ↔ câu nhân viên và bật/tắt tự trả lời theo ý định (thiết kế 04 & 06 A2). */
export function ShadowPage({ isOwner = false }: { isOwner?: boolean }) {
  const cal = useCalibration();
  const list = useComparisons();
  const label = useLabel();
  const sum = useShadowSummary(14);
  const ap = useAutopilotIntents(14);
  const enableAp = useEnableAutopilot();
  const disableAp = useDisableAutopilot();
  const pauseAp = usePauseAutopilot();
  const [apError, setApError] = useState<string | null>(null);
  const apByIntent = new Map<string, AutopilotIntentItem>(
    (ap.data?.items ?? []).map((x) => [x.intent, x]),
  );
  const hasAutopilot = (ap.data?.items ?? []).some((x) => x.state === "autopilot");
  const c = cal.data;
  return (
    <div className="admin-page">
      <div className="row">
        <h1>Chấm nháp</h1>
        {isOwner && (hasAutopilot || ap.data?.auto_send) && (
          <button
            type="button"
            disabled={pauseAp.isPending}
            onClick={() => {
              setApError(null);
              pauseAp.mutate(undefined, {
                onError: (e) => setApError(e instanceof ApiError ? e.message : "Không dừng được."),
              });
            }}
          >
            Dừng tất cả tự trả lời
          </button>
        )}
      </div>
      {apError && (
        <p className="error" role="alert">
          {apError}
        </p>
      )}
      {ap.data && !ap.data.auto_send && hasAutopilot && (
        <p className="muted" role="status">
          Công tắc gửi tự động của shop đang tắt (chỉ lưu nháp Learning).
        </p>
      )}
      {c && (
        <p className={c.calibrated ? "" : "muted"} role="status">
          Đã gán {c.labels}/{c.needs.labels} nhãn
          {c.kappa !== null && <> · đồng thuận với giám khảo (kappa) {c.kappa}</>}
          {c.calibrated ? " · giám khảo đã hiệu chỉnh" : " · chưa đủ để tin điểm giám khảo"}
        </p>
      )}
      <h2>Nguồn mù — Shadow (14 ngày, phiên bản cấu hình hiện tại)</h2>
      {sum.data && sum.data.intents.length === 0 && <p className="muted">Chưa có cặp mù nào.</p>}
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
              <th>Tự trả lời (nguồn mù)</th>
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
                <td title={i.not_ready_because.join("; ")}>
                  {(() => {
                    const item = i.intent ? apByIntent.get(i.intent) : undefined;
                    if (item?.state === "autopilot") {
                      return (
                        <span className="row">
                          <span className="badge approved">Đang tự trả lời</span>
                          {isOwner && i.intent && (
                            <button
                              type="button"
                              className="link"
                              disabled={disableAp.isPending}
                              onClick={() => disableAp.mutate(i.intent!)}
                            >
                              Tắt
                            </button>
                          )}
                        </span>
                      );
                    }
                    if (isOwner && i.intent && item?.can_enable) {
                      return (
                        <button
                          type="button"
                          disabled={enableAp.isPending}
                          onClick={() => {
                            setApError(null);
                            enableAp.mutate(
                              { intent: i.intent!, source: item.enable_source ?? undefined },
                              {
                                onError: (e) =>
                                  setApError(e instanceof ApiError ? e.message : "Không bật được."),
                              },
                            );
                          }}
                        >
                          Bật tự trả lời
                        </button>
                      );
                    }
                    if (item?.demote_reason) {
                      return `Đã hạ cấp: ${item.demote_reason}`;
                    }
                    return i.ready ? "Đủ điều kiện đề xuất" : i.not_ready_because[0];
                  })()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Nguồn đã thấy — Learning (đo theo mức sửa, 14 ngày)</h2>
      {sum.data && (sum.data.learning_intents ?? []).length === 0 && (
        <p className="muted">Chưa có nháp đã hiện cho nhân viên.</p>
      )}
      {sum.data && (sum.data.learning_intents ?? []).length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Ý định</th>
              <th>Đã chọn</th>
              <th>Dùng nguyên</th>
              <th>Sửa nhẹ</th>
              <th>Bỏ</th>
              <th>Gửi được</th>
              <th>Sai</th>
              <th>Tự trả lời (nguồn Learning)</th>
            </tr>
          </thead>
          <tbody>
            {(sum.data.learning_intents ?? []).map((i) => (
              <tr key={i.intent ?? "-"}>
                <td>{i.intent ?? "—"}</td>
                <td>
                  {i.scored}/{i.total}
                </td>
                <td>{pct(i.as_is_rate)}</td>
                <td>{pct(i.minor_edit_rate)}</td>
                <td>{pct(i.discarded_rate)}</td>
                <td>{pct(i.sendable_rate)}</td>
                <td>{pct(i.wrong_rate)}</td>
                <td title={i.not_ready_because.join("; ")}>
                  {i.ready ? "Đủ điều kiện đề xuất" : i.not_ready_because[0]}
                </td>
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
