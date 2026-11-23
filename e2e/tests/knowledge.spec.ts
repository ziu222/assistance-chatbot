import { expect, test } from "@playwright/test";
import { staffLogin } from "./helpers";

// Bước 2.13 (thiết kế §13.3): trang Kiến thức cho biết chủ đề nào shop chưa viết; thêm FAQ thì
// chủ đề đó chuyển sang đã có.
test("thêm FAQ bảo hành ⇒ độ phủ đánh dấu bảo hành đã có", async ({ browser }) => {
  const staff = await staffLogin(browser);
  await staff.getByRole("navigation", { name: "Màn hình" }).getByRole("button", { name: "Kiến thức" }).click();
  const coverage = staff.getByRole("region", { name: "Độ phủ kiến thức" });
  await expect(coverage.getByText(/Khi khách hỏi, bot sẽ nhờ nhân viên/)).toBeVisible();

  const question = `Bảo hành bao lâu? ${Date.now()}`;
  const form = staff.getByRole("region", { name: "Thêm câu hỏi" });
  await form.getByLabel("Câu hỏi").fill(question);
  await form.getByLabel("Trả lời").fill("Bảo hành 6 tháng cho lỗi nhà sản xuất.");
  await form.getByRole("button", { name: "Thêm" }).click();

  const row = staff.getByRole("listitem").filter({ hasText: question });
  try {
    await expect(coverage.getByRole("listitem").filter({ hasText: question })).toContainText("✓ bảo hành");
  } finally {
    await row.getByRole("button", { name: "Lưu trữ" }).click();
    await expect(row).toHaveCount(0);
  }
});
