import type { FactCheck, FactKind } from "./api";

const LABEL: Record<FactKind, string> = {
  amounts: "số tiền",
  durations: "thời hạn",
  times: "giờ",
  phones: "số điện thoại",
  links: "link",
  variables: "biến",
};

const show = (kind: FactKind, v: string) =>
  kind === "amounts" ? Number(v).toLocaleString("vi-VN") + "đ" : kind === "durations" ? prettyDuration(v) : v;

function prettyDuration(v: string): string {
  const units: Record<string, string> = { ngay: "ngày", tuan: "tuần", thang: "tháng", nam: "năm", gio: "giờ", phut: "phút" };
  const [n, u] = v.split(" ");
  return `${n} ${units[u] ?? u}`;
}

/** Câu giải thích cho chủ shop vì sao một gợi ý bị khóa hoặc cần xem lại. */
export function factProblems(fc: FactCheck): string[] {
  const lines: string[] = [];
  for (const [kind, values] of Object.entries(fc.missing) as [FactKind, string[]][]) {
    lines.push(`Mất ${LABEL[kind]}: ${values.map((v) => show(kind, v)).join(", ")}`);
  }
  for (const [kind, values] of Object.entries(fc.added) as [FactKind, string[]][]) {
    lines.push(`Tự thêm ${LABEL[kind]}: ${values.map((v) => show(kind, v)).join(", ")}`);
  }
  if (fc.new_promises.length) lines.push(`Có lời hứa mới bản gốc không có: ${fc.new_promises.join(", ")}`);
  return lines;
}

const ERRORS: Record<string, string> = {
  daily_cap: "Đã hết lượt gợi ý hôm nay. Thử lại vào ngày mai.",
  llm_unavailable: "Trợ lý đang bận, thử lại sau ít phút.",
  refusal: "Trợ lý không viết lại được đoạn này.",
  invalid_output: "Trợ lý trả về kết quả lỗi, thử lại.",
};
export const writingError = (code: string | null) => (code ? (ERRORS[code] ?? "Không gợi ý được, thử lại.") : null);
