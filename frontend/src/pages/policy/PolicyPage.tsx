import { useState } from "react";
import {
  usePolicy,
  useReleases,
  useRestoreRelease,
  useSavePolicy,
  useAutoSend,
  useSimulation,
  type BusinessHours,
  type Day,
  type PolicyConfig,
  type SituationConfig,
  type Span,
} from "../../entities/policy/api";
import { DAYS, clock, describeOutcome, localInstant, normalizeSpans, toLocalInput } from "../../entities/policy/form";
import { ApiError } from "../../shared/api/client";
import { formatTime } from "../../shared/lib/format";

// ponytail: giờ nhập/hiển thị theo giờ máy người dùng; đúng khi máy cùng múi giờ với shop
// (shop Việt Nam). Thêm chọn múi giờ khi có shop ở múi khác.
const EMPTY_HOURS: BusinessHours = { timezone: "Asia/Ho_Chi_Minh", weekly: {}, closed_days: [], pause_until: null };

function normalize(raw: PolicyConfig): PolicyConfig {
  const c = { ...raw, handoff_topics: raw.handoff_topics ?? [] };
  if (!c.hours) return c;
  const weekly: BusinessHours["weekly"] = {};
  for (const { id } of DAYS) if (c.hours.weekly[id]?.length) weekly[id] = normalizeSpans(c.hours.weekly[id]);
  return { ...c, hours: { ...c.hours, weekly } };
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Bạn không có quyền sửa cấu hình này.";
    if (e.status === 409) return "Người khác vừa lưu. Tải lại trang rồi thử lại.";
    if (e.status === 422) return "Có ô chưa hợp lệ: kiểm tra giờ (giờ mở và đóng không được trùng) và nội dung tin.";
  }
  return "Không lưu được. Thử lại sau.";
}

function HoursEditor({ hours, onChange }: { hours: BusinessHours; onChange: (h: BusinessHours) => void }) {
  const setDay = (day: Day, spans: Span[]) => onChange({ ...hours, weekly: { ...hours.weekly, [day]: spans } });
  return (
    <>
      <table className="table hours">
        <tbody>
          {DAYS.map(({ id, label }) => {
            const spans = hours.weekly[id] ?? [];
            return (
              <tr key={id}>
                <th scope="row">
                  <label className="row">
                    <input
                      type="checkbox"
                      checked={spans.length > 0}
                      onChange={(e) => setDay(id, e.target.checked ? [["08:00", "21:00"]] : [])}
                    />
                    {label}
                  </label>
                </th>
                <td>
                  {spans.length === 0 && <span className="muted">Nghỉ</span>}
                  {spans.map(([a, b], i) => (
                    <span key={i} className="row span">
                      <input
                        type="time"
                        aria-label={`${label}: mở từ`}
                        value={a}
                        onChange={(e) => setDay(id, spans.map((s, j) => (j === i ? [e.target.value, s[1]] : s)))}
                      />
                      –
                      <input
                        type="time"
                        aria-label={`${label}: đến`}
                        value={b}
                        onChange={(e) => setDay(id, spans.map((s, j) => (j === i ? [s[0], e.target.value] : s)))}
                      />
                      {spans.length > 1 && (
                        <button type="button" className="link" onClick={() => setDay(id, spans.filter((_, j) => j !== i))}>
                          Bỏ
                        </button>
                      )}
                    </span>
                  ))}
                  {spans.length > 0 && spans.length < 4 && (
                    <button type="button" className="link" onClick={() => setDay(id, [...spans, ["13:30", "21:00"]])}>
                      + Thêm khoảng (vd nghỉ trưa)
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted">Giờ đóng nhỏ hơn giờ mở nghĩa là mở qua nửa đêm (vd 20:00 – 02:00).</p>

      <h3>Ngày nghỉ</h3>
      <ul className="plain stack">
        {hours.closed_days.map((d, i) => (
          <li key={i} className="row">
            <input
              type="date"
              aria-label="Ngày nghỉ"
              value={d.date}
              onChange={(e) =>
                onChange({
                  ...hours,
                  closed_days: hours.closed_days.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)),
                })
              }
            />
            <input
              placeholder="Ghi chú (vd Mùng 1 Tết)"
              aria-label="Ghi chú ngày nghỉ"
              value={d.note}
              maxLength={100}
              onChange={(e) =>
                onChange({
                  ...hours,
                  closed_days: hours.closed_days.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)),
                })
              }
            />
            <button
              type="button"
              className="link"
              onClick={() => onChange({ ...hours, closed_days: hours.closed_days.filter((_, j) => j !== i) })}
            >
              Bỏ
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() =>
          onChange({ ...hours, closed_days: [...hours.closed_days, { date: new Date().toISOString().slice(0, 10), note: "" }] })
        }
      >
        + Thêm ngày nghỉ
      </button>

      <h3>Tạm nghỉ</h3>
      <div className="row">
        <label>
          Tạm nghỉ đến{" "}
          <input
            type="datetime-local"
            value={hours.pause_until ? toLocalInput(hours.pause_until) : ""}
            onChange={(e) =>
              onChange({ ...hours, pause_until: e.target.value ? new Date(e.target.value).toISOString() : null })
            }
          />
        </label>
        {hours.pause_until && (
          <button type="button" className="link" onClick={() => onChange({ ...hours, pause_until: null })}>
            Mở lại ngay
          </button>
        )}
      </div>
      <p className="muted">Tạm nghỉ tự hết khi tới giờ, không cần nhớ tắt.</p>
    </>
  );
}

const SITUATIONS: { key: "welcome" | "after_hours" | "paused"; title: string; when: string }[] = [
  { key: "welcome", title: "Chào khách", when: "Khách nhắn lần đầu, hoặc quay lại sau một thời gian." },
  { key: "after_hours", title: "Ngoài giờ làm việc", when: "Khách nhắn khi shop đã đóng cửa." },
  { key: "paused", title: "Tạm nghỉ", when: "Khách nhắn khi shop đang tạm nghỉ." },
];

function SituationCard({
  title,
  when,
  value,
  onChange,
  extra,
}: {
  title: string;
  when: string;
  value: SituationConfig;
  onChange: (v: SituationConfig) => void;
  extra?: React.ReactNode;
}) {
  const insert = (v: string) => onChange({ ...value, text: `${value.text}${value.text.endsWith(" ") || !value.text ? "" : " "}${v}` });
  return (
    <article className="card stack" aria-label={title}>
      <label className="row">
        <input type="checkbox" checked={value.enabled} onChange={(e) => onChange({ ...value, enabled: e.target.checked })} />
        <strong>{title}</strong>
      </label>
      <small className="muted">{when}</small>
      <textarea
        aria-label={`Nội dung: ${title}`}
        rows={3}
        maxLength={500}
        value={value.text}
        onChange={(e) => onChange({ ...value, text: e.target.value })}
      />
      <div className="row">
        <small className="muted">Chèn:</small>
        <button type="button" className="link" onClick={() => insert("{shop_name}")}>
          Tên shop
        </button>
        <button type="button" className="link" onClick={() => insert("{next_open}")}>
          Giờ mở cửa lại
        </button>
      </div>
      <label className="row">
        Sau tin này{" "}
        <select value={value.next} onChange={(e) => onChange({ ...value, next: e.target.value as SituationConfig["next"] })}>
          <option value="bot">bot tư vấn tiếp</option>
          <option value="waiting_human">bot im, chờ nhân viên</option>
        </select>
      </label>
      {extra}
    </article>
  );
}

function Preview({ config }: { config: PolicyConfig }) {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [minutes, setMinutes] = useState(() => new Date().getHours() * 60);
  const [returning, setReturning] = useState(false);
  const sim = useSimulation({
    config,
    at: localInstant(date, minutes),
    is_new_conversation: !returning,
    minutes_since_last_message: returning ? 2 : null,
  });
  return (
    <section className="card stack" aria-labelledby="preview">
      <h2 id="preview">Xem thử</h2>
      <div className="row">
        <input type="date" aria-label="Ngày xem thử" value={date} onChange={(e) => setDate(e.target.value)} />
        <input
          type="range"
          aria-label="Giờ xem thử"
          min={0}
          max={1439}
          step={10}
          value={minutes}
          onChange={(e) => setMinutes(Number(e.target.value))}
        />
        <strong>{clock(minutes)}</strong>
      </div>
      <label className="row">
        <input type="checkbox" checked={returning} onChange={(e) => setReturning(e.target.checked)} />
        Khách đang nhắn dở (không phải tin đầu tiên)
      </label>
      <div className="phone" aria-live="polite">
        <div className="bubble in">Shop ơi cho em hỏi</div>
        {sim.data?.text && <div className="bubble out">{sim.data.text}</div>}
      </div>
      {sim.isError && <p className="error">Nội dung chưa hợp lệ nên chưa xem thử được.</p>}
      {sim.data && <p className="muted">{describeOutcome(sim.data)}</p>}
    </section>
  );
}

function Editor({ initial, canManage, currentNumber }: { initial: PolicyConfig; canManage: boolean; currentNumber: number | null }) {
  const [draft, setDraft] = useState(initial);
  const [topicsText, setTopicsText] = useState((initial.handoff_topics ?? []).join("\n"));
  const save = useSavePolicy();
  const set = <K extends keyof PolicyConfig>(k: K, v: PolicyConfig[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="hours">
        <h2 id="hours">Giờ làm việc</h2>
        <label className="row">
          <input type="checkbox" checked={draft.hours === null} onChange={(e) => set("hours", e.target.checked ? null : EMPTY_HOURS)} />
          Không có giờ cố định (luôn coi là đang mở)
        </label>
        {draft.hours && <HoursEditor hours={draft.hours} onChange={(h) => set("hours", h)} />}
      </section>

      <section aria-labelledby="auto">
        <h2 id="auto">Tin tự động</h2>
        <p className="muted">Mỗi lần khách nhắn, shop gửi tối đa một tin tự động. Thứ tự ưu tiên: tạm nghỉ, ngoài giờ, chào khách.</p>
        <div className="grid">
          {SITUATIONS.map((s) => (
            <SituationCard
              key={s.key}
              title={s.title}
              when={s.when}
              value={draft[s.key]}
              onChange={(v) => set(s.key, { ...draft[s.key], ...v })}
              extra={
                s.key === "welcome" && (
                  <label className="row">
                    Chào lại nếu khách im lặng quá{" "}
                    <input
                      type="number"
                      min={1}
                      max={720}
                      value={draft.welcome.gap_hours}
                      onChange={(e) => set("welcome", { ...draft.welcome, gap_hours: Number(e.target.value) || 24 })}
                    />{" "}
                    giờ
                  </label>
                )
              }
            />
          ))}
        </div>
      </section>

      <section className="card stack" aria-labelledby="topics">
        <h2 id="topics">Chủ đề luôn cần nhân viên</h2>
        <p className="muted">
          Khách nhắc tới một trong các cụm này (không cần dấu) thì bot dừng và báo nhân viên trong tab "Cần xử lý". Khách
          đòi gặp người ("gặp nhân viên", "khiếu nại"…) luôn được chuyển, không cần thêm ở đây.
        </p>
        <textarea
          aria-label="Chủ đề luôn cần nhân viên, mỗi dòng một cụm"
          rows={4}
          placeholder={"đổi size\nbảo hành\nđơn sỉ"}
          value={topicsText}
          onChange={(e) => {
            setTopicsText(e.target.value);
            set(
              "handoff_topics",
              e.target.value
                .split("\n")
                .map((x) => x.trim())
                .filter((x) => x.length >= 2)
                .slice(0, 30),
            );
          }}
        />
      </section>

      <Preview config={draft} />

      <div className="row save-bar">
        <button type="button" disabled={!canManage || save.isPending} onClick={() => save.mutate(draft)}>
          {save.isPending ? "Đang lưu…" : "Lưu"}
        </button>
        <span className="muted">
          {currentNumber ? `Đang dùng phiên bản #${currentNumber}` : "Chưa lưu lần nào: đây là bản gợi ý, chưa có hiệu lực."}
        </span>
        {save.isError && (
          <span className="error" role="alert">
            {errorText(save.error)}
          </span>
        )}
      </div>
    </div>
  );
}

function History({ canManage }: { canManage: boolean }) {
  const releases = useReleases();
  const restore = useRestoreRelease();
  if (!releases.data?.items.length) return null;
  return (
    <section className="card" aria-labelledby="history">
      <h2 id="history">Các phiên bản đã lưu</h2>
      <ul className="plain stack">
        {releases.data.items.map((r) => (
          <li key={r.id} className="row">
            <span>
              #{r.number} · {formatTime(r.created_at)}
            </span>
            {r.current ? (
              <span className="badge ok">Đang dùng</span>
            ) : (
              <button type="button" className="link" disabled={!canManage || restore.isPending} onClick={() => restore.mutate(r.id)}>
                Khôi phục
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function AutoSendSwitch({ hasRelease, on, canManage }: { hasRelease: boolean; on: boolean; canManage: boolean }) {
  const toggle = useAutoSend();
  return (
    <section className="card" aria-labelledby="auto-send">
      <h2 id="auto-send">Gửi tin tự động cho khách</h2>
      <label className="row">
        <input
          type="checkbox"
          checked={on}
          disabled={!canManage || !hasRelease || toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
        />
        {on ? "Đang gửi: khách nhận đúng câu bạn viết dưới đây." : "Đang tắt: tin chỉ được ghi lại trong Trace để bạn kiểm tra."}
      </label>
      {!hasRelease && <small className="muted">Lưu tin tự động trước rồi mới bật được.</small>}
      {toggle.isError && (
        <p className="error" role="alert">
          Không đổi được. Thử lại sau.
        </p>
      )}
    </section>
  );
}

export function PolicyPage({ canManage }: { canManage: boolean }) {
  const policy = usePolicy();
  if (policy.isError)
    return (
      <p className="error" role="alert">
        Không tải được cấu hình.
      </p>
    );
  if (!policy.data) return <p className="state">Đang tải…</p>;
  const { release, suggested, auto_send } = policy.data;
  return (
    <div className="admin-page">
      <h1>Giờ làm việc và tin tự động</h1>
      <AutoSendSwitch hasRelease={release !== null} on={auto_send} canManage={canManage} />
      {/* key: lưu/khôi phục ⇒ phiên bản mới ⇒ bản nháp dựng lại từ server */}
      <Editor
        key={release?.id ?? "suggested"}
        initial={normalize(release?.config ?? suggested)}
        canManage={canManage}
        currentNumber={release?.number ?? null}
      />
      <History canManage={canManage} />
    </div>
  );
}
