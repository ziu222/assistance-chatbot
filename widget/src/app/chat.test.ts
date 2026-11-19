import { describe, expect, it } from "vitest";
import { addPending, initialChat, lastSequence, mergeServer, setPending, type ServerMessage } from "./chat";

const msg = (sequence: number, extra: Partial<ServerMessage> = {}): ServerMessage => ({
  id: `m${sequence}`,
  sequence,
  direction: "inbound",
  sender_type: "customer",
  text: `t${sequence}`,
  occurred_at: "2026-11-20T00:00:00Z",
  client_message_id: null,
  ...extra,
});

describe("mergeServer", () => {
  it("sorts by sequence and is idempotent", () => {
    let s = mergeServer(initialChat, "c1", [msg(2), msg(1)]);
    s = mergeServer(s, "c1", [msg(2), msg(3)]); // tín hiệu trùng / refetch chồng lấn
    expect(s.messages.map((m) => m.sequence)).toEqual([1, 2, 3]);
    expect(lastSequence(s)).toBe(3);
  });

  it("replaces optimistic message once the server copy arrives", () => {
    let s = addPending(initialChat, "cm-1", "xin chào");
    s = setPending(s, "cm-1", { status: "sending" });
    expect(s.pending).toHaveLength(1);
    s = mergeServer(s, "c1", [msg(1, { client_message_id: "cm-1" })]);
    expect(s.pending).toEqual([]);
    expect(s.messages).toHaveLength(1);
  });

  it("starts over when the conversation changes", () => {
    let s = mergeServer(initialChat, "c1", [msg(1), msg(2)]);
    s = mergeServer(s, "c2", [msg(1, { id: "x1" })]);
    expect(s.conversationId).toBe("c2");
    expect(s.messages.map((m) => m.id)).toEqual(["x1"]);
  });

  it("empty conversation keeps pending messages", () => {
    const s = mergeServer(addPending(initialChat, "a", "x"), null, []);
    expect(s.pending.map((p) => p.cmid)).toEqual(["a"]);
    expect(lastSequence(s)).toBe(0);
  });
});

describe("setPending", () => {
  it("marks failure with a reason and keeps order", () => {
    let s = addPending(addPending(initialChat, "a", "1"), "b", "2");
    s = setPending(s, "a", { status: "failed", error: "network" });
    expect(s.pending.map((p) => [p.cmid, p.status])).toEqual([
      ["a", "failed"],
      ["b", "sending"],
    ]);
  });
});
