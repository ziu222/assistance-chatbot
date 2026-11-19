import { describe, expect, it } from "vitest";
import { lastSequence, mergeTimeline, visiblePending, type Message } from "./timeline";

const m = (sequence: number, extra: Partial<Message> = {}): Message => ({
  id: `m${sequence}`,
  sequence,
  direction: "inbound",
  sender_type: "customer",
  sender_ref: null,
  text: `t${sequence}`,
  status: "received",
  occurred_at: "2026-11-20T00:00:00Z",
  client_message_id: `c${sequence}`,
  ...extra,
});

describe("mergeTimeline", () => {
  it("merges incremental pages idempotently in sequence order", () => {
    const a = mergeTimeline([], [m(1), m(2)]);
    const b = mergeTimeline(a, [m(2), m(4), m(3)]);
    expect(b.map((x) => x.sequence)).toEqual([1, 2, 3, 4]);
    expect(lastSequence(b)).toBe(4);
    expect(lastSequence([])).toBe(0);
  });
});

describe("visiblePending", () => {
  it("hides optimistic replies once the server has them", () => {
    const pending = [
      { cmid: "c2", text: "x", status: "sending" as const },
      { cmid: "zz", text: "y", status: "failed" as const },
    ];
    expect(visiblePending(pending, [m(1), m(2)]).map((p) => p.cmid)).toEqual(["zz"]);
  });
});
