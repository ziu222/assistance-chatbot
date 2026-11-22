// Gọi API nhân viên: cookie HttpOnly đi kèm tự động (cùng origin), không lưu token ở client.
// Method không an toàn luôn gửi header chống CSRF mà backend yêu cầu.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly retryAfter?: number,
    readonly details?: string[],
  ) {
    super(code);
  }
}

type Init = { method?: string; body?: unknown; csv?: string };

export async function apiFetch<T>(path: string, init: Init = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "fetch";
  let body: string | undefined;
  if (init.csv !== undefined) {
    headers["Content-Type"] = "text/csv; charset=utf-8";
    body = init.csv;
  } else if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.body);
  }
  const res = await fetch(path, { method, headers, credentials: "same-origin", body });
  if (!res.ok) {
    let code = `http_${res.status}`;
    let details: string[] | undefined;
    try {
      const err = (await res.json()).error;
      code = err?.code ?? code;
      details = err?.details;
    } catch {
      /* không phải JSON */
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, code, Number.isFinite(retry) && retry > 0 ? retry : undefined, details);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}
