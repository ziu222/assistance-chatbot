import { describe, expect, it } from "vitest";
import {
  CHANNEL_FILTERS,
  channelErrorMessage,
  channelLabel,
  channelWindowState,
  formatRemainingWindow,
  isNonRetryableChannelError,
  parseChannelProvider,
} from "./channel";

describe("channelLabel & filters", () => {
  it("maps known providers and keeps unknown provider verbatim", () => {
    expect(channelLabel("webchat")).toBe("Web");
    expect(channelLabel("messenger")).toBe("Messenger");
    expect(channelLabel("zalo_oa")).toBe("Zalo OA");
    expect(channelLabel("shopee")).toBe("shopee");
    expect(CHANNEL_FILTERS.map((f) => f.id)).toEqual([null, "webchat", "messenger", "zalo_oa"]);
    expect(parseChannelProvider("messenger")).toBe("messenger");
    expect(parseChannelProvider("shopee")).toBeNull();
    expect(parseChannelProvider(null)).toBeNull();
  });
});

describe("channelWindowState", () => {
  const now = Date.parse("2026-11-27T10:00:00Z");

  it("returns unlimited when expiresAt is missing", () => {
    expect(channelWindowState(null, now)).toEqual({ status: "unlimited", remainingMinutes: null });
    expect(channelWindowState(undefined, now)).toEqual({ status: "unlimited", remainingMinutes: null });
  });

  it("returns open when more than 120 minutes remain", () => {
    expect(channelWindowState("2026-11-27T13:30:00Z", now)).toEqual({
      status: "open",
      remainingMinutes: 210,
    });
    expect(channelWindowState("2026-11-27T12:01:00Z", now)).toEqual({
      status: "open",
      remainingMinutes: 121,
    });
  });

  it("returns expiring when between 1 and 120 minutes remain", () => {
    expect(channelWindowState("2026-11-27T12:00:00Z", now)).toEqual({
      status: "expiring",
      remainingMinutes: 120,
    });
    expect(channelWindowState("2026-11-27T10:45:00Z", now)).toEqual({
      status: "expiring",
      remainingMinutes: 45,
    });
    expect(channelWindowState("2026-11-27T10:00:10Z", now)).toEqual({
      status: "expiring",
      remainingMinutes: 1,
    });
  });

  it("returns expired when at or past expiry", () => {
    expect(channelWindowState("2026-11-27T10:00:00Z", now)).toEqual({
      status: "expired",
      remainingMinutes: 0,
    });
    expect(channelWindowState("2026-11-27T09:00:00Z", now)).toEqual({
      status: "expired",
      remainingMinutes: 0,
    });
  });
});

describe("formatRemainingWindow", () => {
  it("formats minutes and hours cleanly", () => {
    expect(formatRemainingWindow(0)).toBe("0 phút");
    expect(formatRemainingWindow(45)).toBe("45 phút");
    expect(formatRemainingWindow(60)).toBe("1 giờ");
    expect(formatRemainingWindow(80)).toBe("1 giờ 20 phút");
    expect(formatRemainingWindow(120)).toBe("2 giờ");
  });
});

describe("channelErrorMessage & isNonRetryableChannelError", () => {
  it("maps channel error codes to Vietnamese and marks them non-retryable", () => {
    const codes = [
      "messenger_window_expired",
      "zalo_window_expired",
      "zalo_reply_limit",
      "zalo_quota_exhausted",
      "message_too_long",
    ];
    for (const code of codes) {
      expect(channelErrorMessage(code)).toBeTruthy();
      expect(isNonRetryableChannelError(code)).toBe(true);
    }
    expect(isNonRetryableChannelError("unsupported_provider")).toBe(true);
    expect(channelErrorMessage("http_500")).toBeNull();
    expect(isNonRetryableChannelError("http_500")).toBe(false);
    expect(isNonRetryableChannelError("busy")).toBe(false);
  });
});
