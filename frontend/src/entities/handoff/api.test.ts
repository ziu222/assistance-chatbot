import { describe, expect, it } from "vitest";
import { sourceLabel, waitingLabel } from "./api";

describe("handoff labels", () => {
  const now = new Date("2026-11-23T10:00:00Z");
  it("shows how long the customer has waited", () => {
    expect(waitingLabel("2026-11-23T09:59:40Z", now)).toBe("vừa xong");
    expect(waitingLabel("2026-11-23T09:48:00Z", now)).toBe("chờ 12 phút");
    expect(waitingLabel("2026-11-23T08:00:00Z", now)).toBe("chờ 2 giờ");
    expect(waitingLabel("2026-11-23T07:55:00Z", now)).toBe("chờ 2 giờ 5 phút");
    expect(waitingLabel("2026-11-23T10:01:00Z", now)).toBe("vừa xong"); // lệch đồng hồ
  });
  it("names the topic that triggered the ticket", () => {
    expect(sourceLabel({ source: "topic", reason: "Đổi size" })).toBe("Chủ đề cần nhân viên: Đổi size");
    expect(sourceLabel({ source: "customer_button", reason: null })).toBe("Khách bấm nút");
  });
});
