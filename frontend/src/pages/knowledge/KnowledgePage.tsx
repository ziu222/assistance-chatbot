import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import {
  useArchiveEntry,
  useCatalogSearch,
  useCoverage,
  useCreateEntry,
  useEntries,
  useGaps,
  useImportCatalog,
  useKnowledgeReleases,
  useRestoreKnowledgeRelease,
  useUpdateEntry,
  type DocEntry,
  type FaqEntry,
  type Kind,
} from "../../entities/knowledge/api";
import { buildOnboardingFaqs, missingOnboardingTopics } from "../../entities/knowledge/onboarding";
import {
  KNOWLEDGE_TOPICS,
  gapDraftTemplate,
  topicLabel,
  type GapDraftSeed,
} from "../../entities/knowledge/topics";
import { SuggestPanel } from "../../features/suggest-writing/SuggestPanel";
import { ApiError } from "../../shared/api/client";

const VND = new Intl.NumberFormat("vi-VN");

const FIELDS: Record<Kind, { first: string; second: string; firstLabel: string; secondLabel: string; empty: string }> = {
  faq: { first: "question", second: "answer", firstLabel: "Câu hỏi", secondLabel: "Trả lời", empty: "Chưa có FAQ nào." },
  doc: { first: "title", second: "body", firstLabel: "Tiêu đề", secondLabel: "Nội dung", empty: "Chưa có tài liệu nào." },
};

type Row = (FaqEntry | DocEntry) & Record<string, unknown>;

function TopicSelector({
  value,
  onChange,
}: {
  value: string[] | null;
  onChange: (next: string[] | null) => void;
}) {
  const auto = value === null;
  const selected = new Set(value ?? []);
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange([...next]);
  };
  return (
    <fieldset className="stack">
      <legend>Chủ đề</legend>
      <label className="row">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => onChange(e.target.checked ? null : [])}
        />
        Tự đoán từ nội dung
      </label>
      {!auto && (
        <div className="row">
          {KNOWLEDGE_TOPICS.map((t) => (
            <label key={t.id} className="row">
              <input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)} />
              {t.label}
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function EntryForm({
  kind,
  initial,
  placeholderSecond,
  submitLabel,
  pending,
  onSubmit,
  onCancel,
}: {
  kind: Kind;
  initial?: { first: string; second: string; topics: string[] | null };
  placeholderSecond?: string;
  submitLabel: string;
  pending: boolean;
  onSubmit: (first: string, second: string, topics: string[] | null) => void;
  onCancel?: () => void;
}) {
  const f = FIELDS[kind];
  const [first, setFirst] = useState(initial?.first ?? "");
  const [second, setSecond] = useState(initial?.second ?? "");
  const [topics, setTopics] = useState<string[] | null>(initial ? initial.topics : null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(first.trim(), second.trim(), topics);
    if (!initial) {
      setFirst("");
      setSecond("");
      setTopics(null);
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <label>
        {f.firstLabel}
        <input value={first} required maxLength={kind === "faq" ? 500 : 200} onChange={(e) => setFirst(e.target.value)} />
      </label>
      <label>
        {f.secondLabel}
        <textarea
          value={second}
          required
          rows={kind === "faq" ? 3 : 8}
          maxLength={kind === "faq" ? 4000 : 20000}
          placeholder={placeholderSecond}
          onChange={(e) => setSecond(e.target.value)}
        />
      </label>
      <SuggestPanel text={second} kind={kind === "faq" ? "faq" : "policy"} onUse={setSecond} />
      <TopicSelector value={topics} onChange={setTopics} />
      <div className="row">
        <button type="submit" disabled={pending || !first.trim() || !second.trim()}>
          {submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="link" onClick={onCancel}>
            Hủy
          </button>
        )}
      </div>
    </form>
  );
}

function ContentTab({
  kind,
  canManage,
  draftSeed,
  onClearSeed,
}: {
  kind: Kind;
  canManage: boolean;
  draftSeed?: GapDraftSeed | null;
  onClearSeed?: () => void;
}) {
  const f = FIELDS[kind];
  const list = useEntries<FaqEntry | DocEntry>(kind);
  const create = useCreateEntry(kind);
  const update = useUpdateEntry(kind);
  const archive = useArchiveEntry(kind);
  const [editing, setEditing] = useState<string | null>(null);
  const createRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (draftSeed) createRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [draftSeed]);

  const rows = ((list.data?.items ?? []) as Row[]).filter((e) => e.status !== "archived");
  return (
    <div className="stack">
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.isError && (
        <p className="error" role="alert">
          Không tải được dữ liệu.
        </p>
      )}
      {list.data && rows.length === 0 && <p className="muted">{f.empty}</p>}
      <ul className="plain">
        {rows.map((e) => {
          const firstVal = String(e[f.first] ?? "");
          const secondVal = String(e[f.second] ?? "");
          const effective = e.effective_topics ?? [];
          return (
            <li key={e.id} className="card">
              {editing === e.id ? (
                <EntryForm
                  kind={kind}
                  initial={{ first: firstVal, second: secondVal, topics: e.topics ?? null }}
                  submitLabel="Lưu thay đổi"
                  pending={update.isPending}
                  onCancel={() => setEditing(null)}
                  onSubmit={(first, second, topics) =>
                    update.mutate(
                      { id: e.id, [f.first]: first, [f.second]: second, topics },
                      { onSuccess: () => setEditing(null) },
                    )
                  }
                />
              ) : (
                <>
                  <strong>{firstVal}</strong>
                  <p className="pre">{secondVal}</p>
                  <div className="row">
                    <span className={`badge ${e.status}`}>{e.status === "approved" ? "Đã duyệt" : "Nháp"}</span>
                    {effective.map((t) => (
                      <span key={t} className="badge">
                        {topicLabel(t)}
                      </span>
                    ))}
                    {e.topics === null && effective.length > 0 && <small className="muted">(tự đoán)</small>}
                    {canManage && (
                      <>
                        <button type="button" className="link" onClick={() => setEditing(e.id)}>
                          Sửa
                        </button>
                        <button
                          type="button"
                          className="link"
                          disabled={archive.isPending}
                          onClick={() => archive.mutate(e.id)}
                        >
                          Lưu trữ
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </li>
          );
        })}
      </ul>
      {(update.isError || archive.isError || create.isError) && (
        <p className="error" role="alert">
          Không lưu được thay đổi.
        </p>
      )}
      {canManage && (
        <section ref={createRef} className="card" aria-label={`Thêm ${f.firstLabel.toLowerCase()}`}>
          <h3>{kind === "faq" ? "Thêm FAQ" : "Thêm tài liệu"}</h3>
          <EntryForm
            key={draftSeed ? `seed-${draftSeed.key}` : "new"}
            kind={kind}
            initial={draftSeed ? { first: draftSeed.question, second: "", topics: draftSeed.topics } : undefined}
            placeholderSecond={draftSeed?.hint}
            submitLabel="Thêm"
            pending={create.isPending}
            onCancel={draftSeed ? onClearSeed : undefined}
            onSubmit={(first, second, topics) =>
              create.mutate(
                { [f.first]: first, [f.second]: second, ...(topics !== null ? { topics } : {}) },
                { onSuccess: () => onClearSeed?.() },
              )
            }
          />
        </section>
      )}
    </div>
  );
}

const CSV_HEADER = "sku,name,description,price_vnd,size,color,stock";

function CatalogTab({ canManage }: { canManage: boolean }) {
  const importer = useImportCatalog();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const search = useCatalogSearch(term);

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // chọn lại cùng file vẫn kích hoạt
    if (file) importer.mutate(await file.text());
  };
  const err = importer.error instanceof ApiError ? importer.error : null;

  return (
    <div className="stack">
      {canManage && (
        <section className="card" aria-labelledby="catalog-import">
          <h3 id="catalog-import">Nhập catalog từ CSV</h3>
          <p className="muted">
            UTF-8, tối đa 2 MB, cột: <code>{CSV_HEADER}</code> (thêm cột <code>url</code> nếu muốn bot gửi link trang sản
            phẩm). Cập nhật theo <code>sku</code>; một dòng lỗi thì không nhập gì cả.
          </p>
          <input type="file" accept=".csv,text/csv" onChange={onFile} disabled={importer.isPending} aria-label="Chọn file CSV" />
          {importer.isPending && <p className="state">Đang nhập…</p>}
          {importer.data && (
            <p role="status">
              Đã nhập {importer.data.items} dòng: tạo mới {importer.data.created}, cập nhật {importer.data.updated}.
            </p>
          )}
          {err && (
            <div className="error" role="alert">
              <p>{err.code === "invalid_csv" ? "File CSV có lỗi, chưa nhập gì:" : "Không nhập được file."}</p>
              {err.details && (
                <ul>
                  {err.details.slice(0, 20).map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}
      <section className="card" aria-labelledby="catalog-search">
        <h3 id="catalog-search">Thử tìm sản phẩm (như agent)</h3>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            setTerm(q.trim());
          }}
        >
          <input value={q} placeholder="vd: ao khoac gio" maxLength={100} onChange={(e) => setQ(e.target.value)} aria-label="Từ khóa" />
          <button type="submit" disabled={!q.trim()}>
            Tìm
          </button>
        </form>
        {search.isFetching && <p className="state">Đang tìm…</p>}
        {search.data && search.data.items.length === 0 && <p className="muted">Không có kết quả.</p>}
        <ul className="plain">
          {search.data?.items.map((p) => (
            <li key={p.id} className="product">
              <strong>{p.name}</strong> <small className="muted">{p.sku}</small>
              <div>{VND.format(p.price_vnd)} đ</div>
              <small className="muted">
                {p.variants.map((v) => `${[v.size, v.color].filter(Boolean).join("/") || "—"}: ${v.stock ?? 0}`).join(" · ")}
              </small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Hỏi nhanh 4 chủ đề phổ biến khi shop mới thiết lập (bước 2 §13.3); để trống mục nào thì bỏ qua mục đó. */
function QuickOnboardingCard({ canManage }: { canManage: boolean }) {
  const coverage = useCoverage();
  const create = useCreateEntry("faq");
  const [hidden, setHidden] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  if (!canManage || !coverage.data) return null;
  const missing = missingOnboardingTopics(coverage.data.items);
  if (missing.length === 0) return null;

  const payloads = buildOnboardingFaqs(answers);
  const saveAll = async (e: FormEvent) => {
    e.preventDefault();
    if (payloads.length === 0) return;
    setSaving(true);
    setNotice(null);
    setErrMsg(null);
    let saved = 0;
    try {
      for (const item of payloads) {
        await create.mutateAsync(item);
        saved += 1;
        setAnswers((prev) => ({ ...prev, [item.topics[0]]: "" }));
      }
      setNotice(`Đã lưu ${saved} FAQ cốt lõi.`);
    } catch {
      setErrMsg(saved > 0 ? `Đã lưu ${saved} mục, nhưng có mục tiếp theo bị lỗi.` : "Không lưu được, thử lại sau.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card stack" aria-labelledby="quick-onboarding">
      <div className="row">
        <h3 id="quick-onboarding">Thiết lập nhanh kiến thức shop</h3>
        <button type="button" className="link" onClick={() => setHidden((h) => !h)}>
          {hidden ? `Mở (${missing.length} mục chưa có)` : "Thu gọn"}
        </button>
      </div>
      {!hidden && (
        <form className="stack" onSubmit={saveAll}>
          <p className="muted">
            Trả lời nhanh các câu hỏi khách hay hỏi nhất (có thể bỏ qua mục chưa cần).
          </p>
          {missing.map((t) => {
            const val = answers[t.id] ?? "";
            return (
              <div key={t.id} className="stack">
                <label>
                  <strong>{t.sampleQuestion}</strong>
                  <textarea
                    rows={2}
                    maxLength={4000}
                    placeholder={t.placeholderAnswer}
                    value={val}
                    onChange={(e) => setAnswers((prev) => ({ ...prev, [t.id]: e.target.value }))}
                  />
                </label>
                <SuggestPanel
                  text={val}
                  kind="faq"
                  onUse={(text) => setAnswers((prev) => ({ ...prev, [t.id]: text }))}
                />
              </div>
            );
          })}
          {notice && <p role="status">{notice}</p>}
          {errMsg && (
            <p className="error" role="alert">
              {errMsg}
            </p>
          )}
          <div className="row">
            <button type="submit" disabled={saving || payloads.length === 0}>
              {saving ? "Đang lưu…" : `Lưu ${payloads.length || ""} mục đã điền`}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

/** Shop không cần viết đủ mọi thứ; chỉ cần biết chỗ nào còn thiếu và bot sẽ làm gì ở đó. */
function CoverageCard({ canManage, onWrite }: { canManage: boolean; onWrite: (topic: string) => void }) {
  const coverage = useCoverage();
  const gaps = useGaps(7);
  if (!coverage.data) return null;
  const missing = coverage.data.items.filter((c) => !c.covered);
  const asked = gaps.data?.items ?? [];
  return (
    <section className="card" aria-labelledby="coverage">
      <h3 id="coverage">Độ phủ kiến thức</h3>
      <ul className="plain">
        {coverage.data.items.map((c) => (
          <li key={c.topic} className="row">
            <span>
              {c.covered ? "✓" : "○"} {c.label}
              {c.covered && <small className="muted"> — {c.sources.map((x) => x.title).join(", ")}</small>}
            </span>
            {!c.covered && canManage && (
              <button type="button" className="link" onClick={() => onWrite(c.topic)}>
                Viết câu trả lời
              </button>
            )}
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <p className="muted">
          Bạn chưa có thông tin về {missing.map((c) => c.label).join(", ")}. Khi khách hỏi, bot sẽ nhờ nhân viên thay vì đoán.
        </p>
      )}
      {asked.length > 0 && (
        <>
          <h4>Khách hỏi mà bot chưa có nguồn (7 ngày qua)</h4>
          <ul className="plain">
            {asked.map((g) => (
              <li key={g.topic} className="row">
                <span>
                  {g.label}: {g.count} lần
                </span>
                {canManage && (
                  <button type="button" className="link" onClick={() => onWrite(g.topic)}>
                    Viết câu trả lời
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function ReleasesTab({ canManage }: { canManage: boolean }) {
  const list = useKnowledgeReleases();
  const restore = useRestoreKnowledgeRelease();
  const items = list.data?.items ?? [];

  return (
    <div className="stack">
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.isError && (
        <p className="error" role="alert">
          Không tải được lịch sử bản phát hành.
        </p>
      )}
      {list.data && items.length === 0 && (
        <p className="muted">Chưa có bản phát hành nào. Thêm hoặc sửa FAQ/tài liệu để tạo bản đầu tiên.</p>
      )}
      <ul className="plain">
        {items.map((r) => (
          <li key={r.id} className="card">
            <div className="row">
              <strong>Bản #{r.number}</strong>
              {r.is_current && <span className="badge approved">Đang dùng</span>}
              <span className="muted">{r.reason === "lesson" ? "Bài học" : "Chỉnh tay"}</span>
              <span className="muted">
                {r.faq_count} FAQ · {r.doc_count} tài liệu
              </span>
              <small className="muted">{new Date(r.created_at).toLocaleString("vi-VN")}</small>
              {canManage && !r.is_current && (
                <button
                  type="button"
                  className="link"
                  disabled={restore.isPending}
                  onClick={() => restore.mutate(r.number)}
                >
                  Quay lại bản này
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const TABS: { id: "faq" | "doc" | "catalog" | "releases"; label: string }[] = [
  { id: "faq", label: "FAQ" },
  { id: "doc", label: "Tài liệu" },
  { id: "catalog", label: "Catalog" },
  { id: "releases", label: "Phiên bản" },
];

export function KnowledgePage({ canManage }: { canManage: boolean }) {
  const [tab, setTab] = useState<"faq" | "doc" | "catalog" | "releases">("faq");
  const [draftSeed, setDraftSeed] = useState<GapDraftSeed | null>(null);
  const handleWriteGap = (topic: string) => {
    setDraftSeed(gapDraftTemplate(topic));
    setTab("faq");
  };
  return (
    <div className="admin-page">
      <h1>Kiến thức</h1>
      {!canManage && <p className="muted">Bạn chỉ có quyền xem.</p>}
      <QuickOnboardingCard canManage={canManage} />
      <CoverageCard canManage={canManage} onWrite={handleWriteGap} />
      <div role="tablist" className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "catalog" ? (
          <CatalogTab canManage={canManage} />
        ) : tab === "releases" ? (
          <ReleasesTab canManage={canManage} />
        ) : (
          <ContentTab
            key={tab}
            kind={tab}
            canManage={canManage}
            draftSeed={tab === "faq" ? draftSeed : null}
            onClearSeed={() => setDraftSeed(null)}
          />
        )}
      </div>
    </div>
  );
}
