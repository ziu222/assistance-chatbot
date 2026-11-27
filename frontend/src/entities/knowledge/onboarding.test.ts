import { describe, expect, it } from "vitest";
import { buildOnboardingFaqs, missingOnboardingTopics } from "./onboarding";

describe("knowledge quick onboarding", () => {
  it("returns only uncovered core topics in fixed order", () => {
    const missing = missingOnboardingTopics([
      { topic: "hours", label: "giờ làm việc", covered: true, sources: [] },
      { topic: "shipping", label: "phí giao hàng", covered: false, sources: [] },
      { topic: "payment", label: "thanh toán và COD", covered: false, sources: [] },
      { topic: "returns", label: "đổi trả", covered: true, sources: [] },
    ]);
    expect(missing.map((m) => m.id)).toEqual(["shipping", "payment"]);
  });

  it("builds approved FAQ payloads only for non-empty answers", () => {
    const payloads = buildOnboardingFaqs({
      hours: "  ",
      shipping: "Nội thành 20k, tỉnh 30k.",
      returns: "Đổi size trong 7 ngày.",
    });
    expect(payloads).toEqual([
      {
        question: "Phí giao hàng và thời gian nhận hàng thế nào?",
        answer: "Nội thành 20k, tỉnh 30k.",
        topics: ["shipping"],
        status: "approved",
      },
      {
        question: "Chính sách đổi trả của shop thế nào?",
        answer: "Đổi size trong 7 ngày.",
        topics: ["returns"],
        status: "approved",
      },
    ]);
  });
});
