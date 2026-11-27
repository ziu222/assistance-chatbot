export type DeliveryStatus = "pending" | "delivered" | "failed";

export type Message = {
  id: string;
  sequence: number;
  direction: "inbound" | "outbound";
  sender_type: "customer" | "human" | "system" | "bot";
  sender_ref: string | null;
  text: string;
  status: "received" | "sent";
  delivery_status?: DeliveryStatus | null;
  occurred_at: string;
  client_message_id: string;
};

export type PendingReply = { cmid: string; text: string; status: "sending" | "failed" };

export function lastSequence(messages: Message[]): number {
  return messages.length ? messages[messages.length - 1].sequence : 0;
}

/** Cursor kéo timeline: lùi trước tin `pending` nhỏ nhất để nhận cập nhật `delivery_status`. */
export function nextAfterSequence(messages: Message[]): number {
  let minPending: number | null = null;
  for (const m of messages) {
    if (m.delivery_status === "pending" && (minPending === null || m.sequence < minPending)) {
      minPending = m.sequence;
    }
  }
  return minPending === null ? lastSequence(messages) : Math.max(0, minPending - 1);
}

/** Idempotent theo sequence: tín hiệu trùng hay refetch chồng lấn không nhân đôi tin. */
export function mergeTimeline(current: Message[], incoming: Message[]): Message[] {
  const bySeq = new Map(current.map((x) => [x.sequence, x]));
  for (const x of incoming) bySeq.set(x.sequence, x);
  return [...bySeq.values()].sort((a, b) => a.sequence - b.sequence);
}

export function visiblePending(pending: PendingReply[], messages: Message[]): PendingReply[] {
  const confirmed = new Set(messages.map((x) => x.client_message_id));
  return pending.filter((p) => !confirmed.has(p.cmid));
}
