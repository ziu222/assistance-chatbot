import { describe, expect, it } from "vitest";
import { factProblems, writingError } from "./facts";

describe("writing fact problems", () => {
  it("explains changed facts in plain words", () => {
    const lines = factProblems({
      ok: false,
      missing: { durations: ["7 ngay"], variables: ["{shop_name}"] },
      added: { durations: ["30 ngay"], amounts: ["25000"] },
      new_promises: ["freeship"],
    });
    expect(lines).toEqual([
      "Mất thời hạn: 7 ngày",
      "Mất biến: {shop_name}",
      "Tự thêm thời hạn: 30 ngày",
      `Tự thêm số tiền: ${(25000).toLocaleString("vi-VN")}đ`,
      "Có lời hứa mới bản gốc không có: freeship",
    ]);
  });

  it("is empty for a clean suggestion and maps errors", () => {
    expect(factProblems({ ok: true, missing: {}, added: {}, new_promises: [] })).toEqual([]);
    expect(writingError("daily_cap")).toMatch(/hết lượt/);
    expect(writingError(null)).toBeNull();
  });
});
