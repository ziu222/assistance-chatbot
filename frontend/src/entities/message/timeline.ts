export type Message = {
  id: string;
  sequence: number;
  direction: "inbound" | "outbound";
  sender_type: "customer" | "human" | "system" | "bot";
  sender_ref: string | null;
  text: string;
  status: "received" | "sent";
  occurred_at: string;
  client_message_id: string;
};

export type PendingReply = { cmid: string; text: string; status: "sending" | "failed" };

export function lastSequence(messages: Message[]): number {
  return messages.length ? messages[messages.length - 1].sequence : 0;
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
