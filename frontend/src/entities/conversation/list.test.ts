import { describe, expect, it } from "vitest";
import { flattenPages, type Conversation } from "./list";

const c = (id: string, at: string): Conversation => ({
  id,
  status: "open",
  last_sequence: 1,
  last_message_at: at,
  last_message_preview: "x",
  unread_count: 0,
  version: 1,
  handling: "bot",
  created_at: at,
  closed_at: null,
  customer: { id: "cu", display_name: "Khách #A1B2" },
  connection: { id: "co", provider: "webchat" },
});

describe("flattenPages", () => {
  it("dedupes conversations that moved between pages after refetch", () => {
    // Keyset refetch: hội thoại có tin mới nhảy lên trang 1 nhưng vẫn còn ở trang 2 cũ.
    const pages = [
      { items: [c("b", "2026-11-20T10:05:00Z"), c("a", "2026-11-20T10:04:00Z")], next_cursor: "x" },
      { items: [c("b", "2026-11-20T10:01:00Z"), c("z", "2026-11-20T09:00:00Z")], next_cursor: null },
    ];
    expect(flattenPages(pages).map((x) => x.id)).toEqual(["b", "a", "z"]);
    expect(flattenPages(pages)[0].last_message_at).toBe("2026-11-20T10:05:00Z");
  });
});
