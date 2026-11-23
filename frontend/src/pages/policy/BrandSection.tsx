import { useState } from "react";
import { useBrandProfile, useSaveBrandProfile, type BrandProfile } from "../../entities/writing/api";

const lines = (s: string) =>
  s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

/** Giọng thương hiệu cho trợ lý viết. Câu mẫu do chủ shop chọn, không tự lấy từ lịch sử chat. */
export function BrandSection({ canManage }: { canManage: boolean }) {
  const query = useBrandProfile();
  if (!query.data) return null;
  return <BrandForm key={query.data.number ?? "new"} initial={query.data.profile} number={query.data.number} canManage={canManage} />;
}

function BrandForm({ initial, number, canManage }: { initial: BrandProfile; number: number | null; canManage: boolean }) {
  const [p, setP] = useState(initial);
  const [terms, setTerms] = useState({
    preferred: initial.preferred_terms.join("\n"),
    banned: initial.banned_terms.join("\n"),
    samples: initial.samples.join("\n"),
  });
  const save = useSaveBrandProfile();
  const set = <K extends keyof BrandProfile>(k: K, v: BrandProfile[K]) => setP((x) => ({ ...x, [k]: v }));

  return (
    <section className="card stack" aria-labelledby="brand">
      <h2 id="brand">Giọng thương hiệu</h2>
      <p className="muted">Trợ lý viết dùng giọng này khi bạn bấm "Gợi ý". Bot không tự đổi nội dung đã lưu.</p>
      <div className="row">
        <label className="row">
          Shop xưng{" "}
          <select value={p.shop_pronoun} onChange={(e) => set("shop_pronoun", e.target.value as BrandProfile["shop_pronoun"])}>
            {["em", "shop", "mình", "chúng tôi"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label className="row">
          Gọi khách{" "}
          <select
            value={p.customer_pronoun}
            onChange={(e) => set("customer_pronoun", e.target.value as BrandProfile["customer_pronoun"])}
          >
            {["anh/chị", "bạn", "chị", "anh", "quý khách"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label className="row">
          Giọng{" "}
          <select value={p.tone} onChange={(e) => set("tone", e.target.value as BrandProfile["tone"])}>
            <option value="than_thien">thân thiện</option>
            <option value="lich_su">lịch sự</option>
            <option value="tre_trung">trẻ trung</option>
            <option value="chuyen_nghiep">chuyên nghiệp</option>
          </select>
        </label>
        <label className="row">
          <input type="checkbox" checked={p.emoji} onChange={(e) => set("emoji", e.target.checked)} />
          Được dùng emoji
        </label>
      </div>
      <div className="grid">
        <label className="stack">
          Từ nên dùng (mỗi dòng một từ)
          <textarea rows={3} value={terms.preferred} onChange={(e) => setTerms((t) => ({ ...t, preferred: e.target.value }))} />
        </label>
        <label className="stack">
          Từ không dùng
          <textarea rows={3} value={terms.banned} onChange={(e) => setTerms((t) => ({ ...t, banned: e.target.value }))} />
        </label>
      </div>
      <label className="stack">
        Câu mẫu của shop (3–10 câu bạn thích, mỗi dòng một câu)
        <textarea rows={4} value={terms.samples} onChange={(e) => setTerms((t) => ({ ...t, samples: e.target.value }))} />
      </label>
      <div className="row">
        <button
          type="button"
          disabled={!canManage || save.isPending}
          onClick={() =>
            save.mutate({
              ...p,
              preferred_terms: lines(terms.preferred).slice(0, 20),
              banned_terms: lines(terms.banned).slice(0, 20),
              samples: lines(terms.samples).slice(0, 10),
            })
          }
        >
          {save.isPending ? "Đang lưu…" : "Lưu giọng thương hiệu"}
        </button>
        <span className="muted">{number ? `Phiên bản #${number}` : "Chưa lưu"}</span>
        {save.isError && (
          <span className="error" role="alert">
            Không lưu được: mỗi từ tối đa 40 ký tự, mỗi câu mẫu tối đa 300 ký tự.
          </span>
        )}
      </div>
    </section>
  );
}
