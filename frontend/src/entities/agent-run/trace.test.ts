import { describe, expect, it } from "vitest";
import { cacheHitRate, formatCost, reasonLabel, statusLabel, stepSummary, type Step } from "./trace";

const step = (over: Partial<Step>): Step => ({
  index: 0,
  kind: "rule",
  name: null,
  status: "ok",
  latency_ms: 1,
  detail: {},
  ...over,
});

describe("formatCost", () => {
  it("shows micro-USD as dollars", () => {
    expect(formatCost(12_300)).toBe("$0.0123");
    expect(formatCost(2_000_000)).toBe("$2.0000");
  });
  it("never rounds a real cost down to zero", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(3)).toBe("<$0.0001");
  });
});

describe("cacheHitRate", () => {
  it("is the share of input tokens read from cache", () => {
    expect(cacheHitRate({ input_tokens: 100, cache_read_tokens: 300, cache_write_tokens: 0 })).toBe(0.75);
  });
  it("is null when nothing was sent", () => {
    expect(cacheHitRate({ input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 })).toBeNull();
  });
});

describe("labels", () => {
  it("translates known statuses and reasons, passes unknown codes through", () => {
    expect(statusLabel("handoff_suggested")).toBe("Nên chuyển người");
    expect(reasonLabel("daily_cap")).toBe("Hết trần chi phí ngày");
    expect(reasonLabel("something_new")).toBe("something_new");
    expect(reasonLabel(null)).toBeNull();
  });
});

describe("stepSummary", () => {
  it("summarises a tool call from redacted fields only", () => {
    const s = step({ kind: "tool", detail: { arg_keys: ["limit", "query"], records: 2 } });
    expect(stepSummary(s)).toBe("tham số: limit, query · 2 kết quả");
  });
  it("shows provider, tokens and failover for model calls", () => {
    const s = step({
      kind: "llm",
      detail: {
        provider: "openai",
        model: "gpt-4.1",
        tokens: { in: 10, cache_read: 5, out: 3 },
        failover_from: ["anthropic:claude-sonnet-5:timeout"],
      },
    });
    expect(stepSummary(s)).toContain("openai:gpt-4.1");
    expect(stepSummary(s)).toContain("10 vào / 5 cache / 3 ra");
    expect(stepSummary(s)).toContain("anthropic:claude-sonnet-5:timeout");
  });
  it("lists validation problems", () => {
    expect(stepSummary(step({ kind: "validate", detail: { problems: [] } }))).toBe("hợp lệ");
    expect(stepSummary(step({ kind: "validate", detail: { problems: ["amount_mismatch"] } }))).toBe(
      "vấn đề: amount_mismatch",
    );
  });
});

describe("signal steps", () => {
  it("summarizes recorded escalation signals", () => {
    expect(stepSummary(step({ kind: "signal", name: "behavior", detail: { signals: ["repeat", "negation"], score: 3 } }))).toBe(
      "hỏi lặp lại, phủ nhận câu trả lời · điểm 3",
    );
    expect(stepSummary(step({ kind: "signal", detail: { signals: [], score: 0 } }))).toBe("không có · điểm 0");
  });
});
