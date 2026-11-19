// Trạng thái hội thoại phía khách. Server là sự thật; tin lạc quan chỉ sống tới khi bản server về.

export type ServerMessage = {
  id: string;
  sequence: number;
  direction: "inbound" | "outbound";
  sender_type: "customer" | "human" | "system";
  text: string;
  occurred_at: string;
  client_message_id: string | null;
};

export type Pending = {
  cmid: string;
  text: string;
  status: "sending" | "failed";
  error?: string;
};

export type ChatState = {
  conversationId: string | null;
  messages: ServerMessage[];
  pending: Pending[];
};

export const initialChat: ChatState = { conversationId: null, messages: [], pending: [] };

export function lastSequence(s: ChatState): number {
  return s.messages.length ? s.messages[s.messages.length - 1].sequence : 0;
}

export function mergeServer(
  s: ChatState,
  conversationId: string | null,
  incoming: ServerMessage[],
): ChatState {
  // Conversation mới (cuộc cũ đã đóng): không trộn tin của hai cuộc.
  const base = conversationId && conversationId !== s.conversationId && s.conversationId ? [] : s.messages;
  const bySeq = new Map(base.map((m) => [m.sequence, m]));
  for (const m of incoming) bySeq.set(m.sequence, m); // idempotent theo (conversation, sequence)
  const messages = [...bySeq.values()].sort((a, b) => a.sequence - b.sequence);
  const confirmed = new Set(messages.map((m) => m.client_message_id).filter(Boolean));
  return {
    conversationId: conversationId ?? s.conversationId,
    messages,
    pending: s.pending.filter((p) => !confirmed.has(p.cmid)),
  };
}

export function addPending(s: ChatState, cmid: string, text: string): ChatState {
  return { ...s, pending: [...s.pending, { cmid, text, status: "sending" }] };
}

export function setPending(s: ChatState, cmid: string, patch: Partial<Pending>): ChatState {
  return { ...s, pending: s.pending.map((p) => (p.cmid === cmid ? { ...p, ...patch } : p)) };
}
