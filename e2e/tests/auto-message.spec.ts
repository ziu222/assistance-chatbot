import { expect, test } from "@playwright/test";
import { customerSays, openWidget, staffLogin } from "./helpers";

// Bước 2.9 (thiết kế §12.6): chủ shop bật "Gửi tin tự động" ⇒ khách mới nhận đúng câu đã viết.
// Bản gợi ý bật cả chào khách, ngoài giờ và tạm nghỉ nên dù chạy lúc nào cũng có đúng một câu.
test("bật gửi tin tự động ⇒ khách nhận câu chủ shop viết, đánh dấu là tin tự động", async ({ browser }) => {
  const staff = await staffLogin(browser);
  const nav = staff.getByRole("navigation", { name: "Màn hình" });
  await nav.getByRole("button", { name: "Tin tự động" }).click();
  // Spec agent đã lưu phiên bản #1; chạy riêng spec này thì tự lưu.
  if (await staff.getByText("Chưa lưu lần nào").isVisible()) {
    await staff.getByRole("button", { name: "Lưu" }).click();
  }
  await expect(staff.getByText(/Đang dùng phiên bản #\d+/)).toBeVisible();
  const toggle = staff.getByRole("region", { name: "Gửi tin tự động cho khách" }).getByRole("checkbox");
  await toggle.click();
  await expect(staff.getByText(/^Đang gửi:/)).toBeVisible();

  try {
    const { widget } = await openWidget(browser);
    await customerSays(widget, "shop ơi cho em hỏi");
    await expect(widget.getByRole("log").getByText(/hỗ trợ gì|hết giờ làm việc|tạm nghỉ/)).toBeVisible({
      timeout: 30_000,
    });

    // Inbox hiện tin đó kèm nhãn "Tin tự động".
    await nav.getByRole("button", { name: "Hộp thư" }).click();
    await staff.getByRole("button", { name: /shop ơi cho em hỏi|hỗ trợ gì|hết giờ|tạm nghỉ/ }).first().click();
    await expect(staff.getByRole("log").getByText(/Tin tự động ·/)).toBeVisible();
  } finally {
    // Tắt lại để các spec khác (khách mới) không nhận thêm tin.
    await nav.getByRole("button", { name: "Tin tự động" }).click();
    await toggle.click();
    await expect(staff.getByText(/^Đang tắt:/)).toBeVisible();
  }
});
