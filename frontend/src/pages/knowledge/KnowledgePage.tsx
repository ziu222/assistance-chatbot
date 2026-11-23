import { useState, type ChangeEvent, type FormEvent } from "react";
import {
  useArchiveEntry,
  useCatalogSearch,
  useCoverage,
  useCreateEntry,
  useEntries,
  useGaps,
  useImportCatalog,
  useUpdateEntry,
  type DocEntry,
  type FaqEntry,
  type Kind,
} from "../../entities/knowledge/api";
import { ApiError } from "../../shared/api/client";

const VND = new Intl.NumberFormat("vi-VN");

const FIELDS: Record<Kind, { first: string; second: string; firstLabel: string; secondLabel: string; empty: string }> = {
  faq: { first: "question", second: "answer", firstLabel: "Câu hỏi", secondLabel: "Trả lời", empty: "Chưa có FAQ nào." },
  doc: { first: "title", second: "body", firstLabel: "Tiêu đề", secondLabel: "Nội dung", empty: "Chưa có tài liệu nào." },
};

type Row = (FaqEntry | DocEntry) & Record<string, string>;

function EntryForm({
  kind,
  initial,
  submitLabel,
  pending,
  onSubmit,
  onCancel,
}: {
  kind: Kind;
  initial?: { first: string; second: string };
  submitLabel: string;
  pending: boolean;
  onSubmit: (first: string, second: string) => void;
  onCancel?: () => void;
}) {
  const f = FIELDS[kind];
  const [first, setFirst] = useState(initial?.first ?? "");
  const [second, setSecond] = useState(initial?.second ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(first.trim(), second.trim());
    if (!initial) {
      setFirst("");
      setSecond("");
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
          onChange={(e) => setSecond(e.target.value)}
        />
      </label>
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

function ContentTab({ kind, canManage }: { kind: Kind; canManage: boolean }) {
  const f = FIELDS[kind];
  const list = useEntries<FaqEntry | DocEntry>(kind);
  const create = useCreateEntry(kind);
  const update = useUpdateEntry(kind);
  const archive = useArchiveEntry(kind);
  const [editing, setEditing] = useState<string | null>(null);

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
        {rows.map((e) => (
          <li key={e.id} className="card">
            {editing === e.id ? (
              <EntryForm
                kind={kind}
                initial={{ first: e[f.first], second: e[f.second] }}
                submitLabel="Lưu thay đổi"
                pending={update.isPending}
                onCancel={() => setEditing(null)}
                onSubmit={(first, second) =>
                  update.mutate({ id: e.id, [f.first]: first, [f.second]: second }, { onSuccess: () => setEditing(null) })
                }
              />
            ) : (
              <>
                <strong>{e[f.first]}</strong>
                <p className="pre">{e[f.second]}</p>
                <div className="row">
                  <span className={`badge ${e.status}`}>{e.status === "approved" ? "Đã duyệt" : "Nháp"}</span>
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
        ))}
      </ul>
      {(update.isError || archive.isError || create.isError) && (
        <p className="error" role="alert">
          Không lưu được thay đổi.
        </p>
      )}
      {canManage && (
        <section className="card" aria-label={`Thêm ${f.firstLabel.toLowerCase()}`}>
          <h3>{kind === "faq" ? "Thêm FAQ" : "Thêm tài liệu"}</h3>
          <EntryForm
            kind={kind}
            submitLabel="Thêm"
            pending={create.isPending}
            onSubmit={(first, second) => create.mutate({ [f.first]: first, [f.second]: second })}
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
            UTF-8, tối đa 2 MB, cột: <code>{CSV_HEADER}</code>. Cập nhật theo <code>sku</code>; một dòng lỗi thì không nhập
            gì cả.
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

/** Shop không cần viết đủ mọi thứ; chỉ cần biết chỗ nào còn thiếu và bot sẽ làm gì ở đó. */
function CoverageCard({ canManage, onWrite }: { canManage: boolean; onWrite: () => void }) {
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
          <li key={c.topic}>
            {c.covered ? "✓" : "○"} {c.label}
            {c.covered && <small className="muted"> — {c.sources.map((x) => x.title).join(", ")}</small>}
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
              <li key={g.topic}>
                {g.label}: {g.count} lần
              </li>
            ))}
          </ul>
          {canManage && (
            <button type="button" onClick={onWrite}>
              Viết câu trả lời
            </button>
          )}
        </>
      )}
    </section>
  );
}

const TABS: { id: "faq" | "doc" | "catalog"; label: string }[] = [
  { id: "faq", label: "FAQ" },
  { id: "doc", label: "Tài liệu" },
  { id: "catalog", label: "Catalog" },
];

export function KnowledgePage({ canManage }: { canManage: boolean }) {
  const [tab, setTab] = useState<"faq" | "doc" | "catalog">("faq");
  return (
    <div className="admin-page">
      <h1>Kiến thức</h1>
      {!canManage && <p className="muted">Bạn chỉ có quyền xem.</p>}
      <CoverageCard canManage={canManage} onWrite={() => setTab("faq")} />
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
        {tab === "catalog" ? <CatalogTab canManage={canManage} /> : <ContentTab key={tab} kind={tab} canManage={canManage} />}
      </div>
    </div>
  );
}
