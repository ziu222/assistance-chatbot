// Gọi API nhân viên: cookie HttpOnly đi kèm tự động (cùng origin), không lưu token ở client.
// Method không an toàn luôn gửi header chống CSRF mà backend yêu cầu.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly retryAfter?: number,
  ) {
    super(code);
  }
}

export async function apiFetch<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "fetch";
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (!res.ok) {
    let code = `http_${res.status}`;
    try {
      code = (await res.json()).error?.code ?? code;
    } catch {
      /* không phải JSON */
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, code, Number.isFinite(retry) && retry > 0 ? retry : undefined);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}
