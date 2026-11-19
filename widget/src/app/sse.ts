// SSE qua fetch stream: EventSource không gửi được header Authorization (thiết kế D8),
// và token không được nằm trên URL.

export type SSEEvent = { event: string; id: string | undefined; data: string };

export class SSEParser {
  private buffer = "";

  feed(chunk: string): SSEEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const events: SSEEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) !== -1) {
      const frame = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let event = "message";
      let id: string | undefined;
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (!line || line.startsWith(":")) continue; // comment / heartbeat
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "id") id = value;
        else if (field === "data") data.push(value);
      }
      if (data.length) events.push({ event, id, data: data.join("\n") });
    }
    return events;
  }
}

/** Mở một kết nối SSE; resolve khi stream kết thúc, reject khi lỗi (để caller kết nối lại). */
export async function streamEvents(
  url: string,
  token: string,
  onOpen: () => void,
  onEvent: (e: SSEEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    signal,
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    const err = new Error(`sse ${res.status}`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  onOpen();
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SSEParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    for (const e of parser.feed(value)) onEvent(e);
  }
}
