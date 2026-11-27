import { describe, expect, it } from "vitest";
import { KNOWLEDGE_TOPICS, gapDraftTemplate, topicLabel } from "./topics";

describe("knowledge topics", () => {
  it("maps known topic ids to Vietnamese labels", () => {
    expect(KNOWLEDGE_TOPICS).toHaveLength(8);
    expect(topicLabel("shipping")).toBe("phí giao hàng");
    expect(topicLabel("returns")).toBe("đổi trả");
    expect(topicLabel("unknown_topic")).toBe("unknown_topic");
  });

  it("prefills question and topic for known gaps and falls back for other", () => {
    const ship = gapDraftTemplate("shipping", 1);
    expect(ship.topics).toEqual(["shipping"]);
    expect(ship.question).toContain("Phí giao hàng");

    const other = gapDraftTemplate("other", 2);
    expect(other.topics).toBeNull();
    expect(other.question).toBe("");
  });
});
