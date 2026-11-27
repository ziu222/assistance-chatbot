import { describe, expect, it } from "vitest";
import { lastSequence, mergeTimeline, nextAfterSequence, visiblePending, type Message } from "./timeline";

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

  it("overwrites existing message with same sequence to update delivery_status", () => {
    const initial = [
      m(1),
      m(2, { direction: "outbound", sender_type: "human", status: "sent", delivery_status: "pending" }),
    ];
    const updated = mergeTimeline(initial, [
      m(2, { direction: "outbound", sender_type: "human", status: "sent", delivery_status: "delivered" }),
    ]);
    expect(updated).toHaveLength(2);
    expect(updated[1].delivery_status).toBe("delivered");

    const failed = mergeTimeline(updated, [
      m(2, { direction: "outbound", sender_type: "human", status: "sent", delivery_status: "failed" }),
    ]);
    expect(failed[1].delivery_status).toBe("failed");
  });
});

describe("nextAfterSequence", () => {
  it("returns lastSequence when there are no pending deliveries", () => {
    expect(nextAfterSequence([])).toBe(0);
    expect(nextAfterSequence([m(1), m(2, { delivery_status: null }), m(3, { delivery_status: "delivered" })])).toBe(3);
    expect(nextAfterSequence([m(1), m(2, { delivery_status: "failed" })])).toBe(2);
  });

  it("rewinds before the earliest pending message sequence", () => {
    expect(nextAfterSequence([m(1, { delivery_status: "pending" })])).toBe(0);
    expect(
      nextAfterSequence([
        m(1),
        m(3, { delivery_status: "delivered" }),
        m(4, { delivery_status: "pending" }),
        m(6, { delivery_status: "pending" }),
      ]),
    ).toBe(3);
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
