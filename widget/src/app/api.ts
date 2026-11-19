import type { ServerMessage } from "./chat";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly retryAfter?: number,
  ) {
    super(code);
  }
}

export type WidgetConfig = { greeting?: string; theme?: { color?: unknown }; privacy_url?: unknown };
export type Session = { token: string; expires_at: string; config: WidgetConfig };
export type MessagePage = { conversation_id: string | null; messages: ServerMessage[]; has_more: boolean };
export type SendResult = { message_id: string; sequence: number; conversation_id: string };

const BASE = "/public/webchat/v1";

async function call<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body) headers["Content-Type"] = "application/json";
  if (init.token) headers.Authorization = `Bearer ${init.token}`;
  const res = await fetch(BASE + path, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let code = "http_error";
    try {
      code = (await res.json()).error?.code ?? code;
    } catch {
      /* body không phải JSON */
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, code, Number.isFinite(retry) && retry > 0 ? retry : undefined);
  }
  return res.json() as Promise<T>;
}

export const api = {
  session: (publicKey: string, token: string | null) =>
    call<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify(token ? { public_key: publicKey, token } : { public_key: publicKey }),
    }),
  send: (token: string, cmid: string, text: string) =>
    call<SendResult>("/messages", {
      method: "POST",
      token,
      body: JSON.stringify({ client_message_id: cmid, text }),
    }),
  list: (token: string, afterSequence: number) =>
    call<MessagePage>(`/messages?after_sequence=${afterSequence}&limit=100`, { token }),
  eventsUrl: `${BASE}/events`,
};

// Token lưu trong localStorage của origin iframe (thiết kế §9.1). Storage có thể bị chặn.
export const tokenStore = {
  get(publicKey: string): string | null {
    try {
      return localStorage.getItem(`atlas.token.${publicKey}`);
    } catch {
      return null;
    }
  },
  set(publicKey: string, token: string): void {
    try {
      localStorage.setItem(`atlas.token.${publicKey}`, token);
    } catch {
      /* chế độ riêng tư: vẫn chạy, chỉ mất lịch sử khi tải lại */
    }
  },
};
