import type { Handling } from "../agent-run/trace";
import type { Day, Simulation, Span } from "./api";

export const DAYS: { id: Day; label: string }[] = [
  { id: "mon", label: "Thứ Hai" },
  { id: "tue", label: "Thứ Ba" },
  { id: "wed", label: "Thứ Tư" },
  { id: "thu", label: "Thứ Năm" },
  { id: "fri", label: "Thứ Sáu" },
  { id: "sat", label: "Thứ Bảy" },
  { id: "sun", label: "Chủ nhật" },
];

/** Server trả "08:00:00"; ô `<input type="time">` dùng "08:00". */
export const hhmm = (t: string) => t.slice(0, 5);
export const normalizeSpans = (spans: Span[] = []): Span[] => spans.map(([a, b]) => [hhmm(a), hhmm(b)]);

/** Ngày (yyyy-mm-dd) + phút trong ngày, theo giờ máy người dùng ⇒ ISO có múi giờ. */
export function localInstant(date: string, minutes: number): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y, m - 1, d, Math.floor(minutes / 60), minutes % 60).toISOString();
}

export const clock = (minutes: number) =>
  `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

const HANDLING: Record<Handling, string> = {
  bot: "bot tư vấn",
  waiting_human: "chờ nhân viên",
  human: "nhân viên xử lý",
};
export const handlingLabel = (h: Handling) => HANDLING[h];

/** Câu giải thích cho chủ shop: sau tin tự động (nếu có), chuyện gì xảy ra. */
export function describeOutcome(s: Simulation): string {
  if (s.set_handling === "waiting_human") return "Sau đó bot im lặng, chờ nhân viên trả lời.";
  if (s.run_agent) return s.text ? "Sau đó bot tư vấn câu hỏi của khách." : "Bot tư vấn ngay, không có tin tự động.";
  return "Bot không trả lời vì nhân viên đang xử lý.";
}

/** ISO ⇒ giá trị cho `<input type="datetime-local">` theo giờ máy người dùng. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
