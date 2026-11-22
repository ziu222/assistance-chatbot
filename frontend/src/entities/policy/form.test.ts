import { describe, expect, it } from "vitest";
import { clock, describeOutcome, localInstant, normalizeSpans } from "./form";

describe("policy form helpers", () => {
  it("normalizes server times for time inputs", () => {
    expect(normalizeSpans([["08:00:00", "21:30:00"]])).toEqual([["08:00", "21:30"]]);
    expect(normalizeSpans(undefined)).toEqual([]);
  });

  it("formats minutes as a clock", () => {
    expect(clock(0)).toBe("00:00");
    expect(clock(22 * 60 + 5)).toBe("22:05");
  });

  it("builds a local instant", () => {
    const d = new Date(localInstant("2026-11-21", 22 * 60 + 10));
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()]).toEqual([2026, 10, 21, 22, 10]);
  });

  it("explains what happens after the auto message", () => {
    const base = { situation: null, text: null, set_handling: null, run_agent: true } as const;
    expect(describeOutcome(base)).toMatch(/không có tin tự động/);
    expect(describeOutcome({ ...base, situation: "after_hours", text: "x" })).toMatch(/bot tư vấn/);
    expect(describeOutcome({ ...base, text: "x", set_handling: "waiting_human", run_agent: false })).toMatch(
      /chờ nhân viên/,
    );
    expect(describeOutcome({ ...base, run_agent: false })).toMatch(/nhân viên đang xử lý/);
  });
});
