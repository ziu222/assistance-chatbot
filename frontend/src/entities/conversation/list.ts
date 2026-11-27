export type ChannelCapabilities = {
  reply_window_seconds: number | null;
  supports_links: boolean;
  supports_buttons: boolean;
  max_text_length: number;
};

export type Conversation = {
  id: string;
  status: "open" | "closed";
  last_sequence: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  unread_count: number;
  version: number;
  handling: "bot" | "waiting_human" | "human";
  created_at: string;
  closed_at: string | null;
  customer: { id: string; display_name: string };
  connection: { id: string; provider: string };
  capabilities?: ChannelCapabilities | null;
  reply_window_expires_at?: string | null;
};

export type ConversationPage = { items: Conversation[]; next_cursor: string | null };

/** Gộp các trang keyset; giữ bản đầu tiên gặp (trang trên = mới hơn) khi một hội thoại bị lặp. */
export function flattenPages(pages: ConversationPage[]): Conversation[] {
  const seen = new Set<string>();
  const out: Conversation[] = [];
  for (const page of pages) {
    for (const c of page.items) {
      if (!seen.has(c.id)) {
        seen.add(c.id);
        out.push(c);
      }
    }
  }
  return out;
}
