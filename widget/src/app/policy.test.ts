import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { nextStep, readableTextOn, safeColor, safeHttpUrl } from "./policy";

describe("nextStep (gửi tin)", () => {
  it("retries network and 5xx errors up to 3 times with backoff", () => {
    const step = nextStep(new TypeError("Failed to fetch"), 0, false);
    expect(step.kind).toBe("retry");
    expect(nextStep(new ApiError(503, "busy"), 2, false).kind).toBe("retry");
    expect(nextStep(new ApiError(503, "busy"), 3, false).kind).toBe("fail");
  });

  it("refreshes the session once on 401", () => {
    expect(nextStep(new ApiError(401, "invalid_token"), 0, false).kind).toBe("refresh");
    expect(nextStep(new ApiError(401, "invalid_token"), 0, true).kind).toBe("fail");
  });

  it("waits on 429 instead of hammering", () => {
    expect(nextStep(new ApiError(429, "rate_limited", 12), 0, false)).toEqual({ kind: "wait", seconds: 12 });
  });

  it("does not retry validation errors", () => {
    expect(nextStep(new ApiError(422, "invalid_request"), 0, false).kind).toBe("fail");
  });

  it("backoff grows and has jitter bounds", () => {
    const a = nextStep(new TypeError("x"), 0, false);
    const b = nextStep(new TypeError("x"), 2, false);
    if (a.kind !== "retry" || b.kind !== "retry") throw new Error("expected retry");
    expect(a.delayMs).toBeLessThanOrEqual(500);
    expect(b.delayMs).toBeGreaterThanOrEqual(1000);
    expect(b.delayMs).toBeLessThanOrEqual(2000);
  });
});

describe("cấu hình tenant không tin cậy", () => {
  it("only allows http(s) links", () => {
    expect(safeHttpUrl("https://shop.vn/privacy")).toBe("https://shop.vn/privacy");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
    expect(safeHttpUrl(undefined)).toBeNull();
  });

  it("picks readable text color on brand color", () => {
    expect(readableTextOn("#ffeb3b")).toBe("#000"); // vàng
    expect(readableTextOn("#0b6e4f")).toBe("#fff"); // xanh đậm
    expect(readableTextOn("#fff")).toBe("#000");
  });

  it("only allows hex colors", () => {
    expect(safeColor("#0a7")).toBe("#0a7");
    expect(safeColor("#00aa77")).toBe("#00aa77");
    expect(safeColor("red; background: url(x)")).toBeNull();
    expect(safeColor(42)).toBeNull();
  });
});
