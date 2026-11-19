import { ApiError } from "./api";

export const MAX_SEND_RETRIES = 3;

export type Step =
  | { kind: "retry"; delayMs: number }
  | { kind: "refresh" }
  | { kind: "wait"; seconds: number }
  | { kind: "fail" };

/** Gửi tin lỗi thì làm gì (thiết kế §9.2). Mọi lần thử dùng CÙNG client_message_id. */
export function nextStep(error: unknown, attempt: number, refreshed: boolean): Step {
  if (error instanceof ApiError) {
    if (error.status === 401) return refreshed ? { kind: "fail" } : { kind: "refresh" };
    if (error.status === 429) return { kind: "wait", seconds: error.retryAfter ?? 5 };
    if (error.status < 500) return { kind: "fail" }; // 4xx khác: thử lại vô ích
  }
  if (attempt >= MAX_SEND_RETRIES) return { kind: "fail" };
  return { kind: "retry", delayMs: backoff(attempt, 500) };
}

/** Lũy thừa 2 có jitter: [base*2^n / 2, base*2^n]. */
export function backoff(attempt: number, baseMs: number, maxMs = 30_000): number {
  const ceil = Math.min(maxMs, baseMs * 2 ** attempt);
  return ceil / 2 + Math.random() * (ceil / 2);
}

// Cấu hình do tenant nhập: coi là không tin cậy trước khi đưa vào DOM/CSS.
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export function safeColor(value: unknown): string | null {
  return typeof value === "string" && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value) ? value : null;
}

/** Chữ đen hay trắng trên nền màu thương hiệu, chọn theo độ tương phản WCAG cao hơn. */
export function readableTextOn(hex: string): "#000" | "#fff" {
  const h = hex.length === 4 ? [...hex.slice(1)].map((c) => c + c).join("") : hex.slice(1);
  const lin = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const L = 0.2126 * lin(0) + 0.7152 * lin(2) + 0.0722 * lin(4);
  return (L + 0.05) / 0.05 > 1.05 / (L + 0.05) ? "#000" : "#fff";
}
