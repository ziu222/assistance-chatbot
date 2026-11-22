import { expect, test } from "@playwright/test";
import { customerSays, openWidget, staffLogin } from "./helpers";

// Tiêu chí chấp nhận bước 2 (thiết kế §11.3.1, §8.2): admin tạo agent, bật Shadow và AI;
// khách nhắn ⇒ trace hiện nháp có căn cứ; nháp không bao giờ tới khách.
// Dùng câu chào để đi đường rule (không gọi LLM), nên CI không cần API key.
test("khách chào ⇒ trace hiện nháp có căn cứ và nháp không tới khách", async ({ browser }) => {
  const staff = await staffLogin(browser);

  // Agent: tạo từ template (bắt đầu Manual), bật Shadow, bật AI của cửa hàng.
  await staff.getByRole("navigation", { name: "Màn hình" }).getByRole("button", { name: "Agent" }).click();
  await staff.getByLabel("Tên agent").fill("Bot E2E");
  await staff.getByRole("button", { name: "Tạo agent" }).click();
  const card = staff.getByRole("article", { name: "Bot E2E" });
  await expect(card).toBeVisible();
  await card.getByLabel("Chế độ").selectOption("shadow");
  await expect(card.getByLabel("Chế độ")).toHaveValue("shadow");
  // Ô bật/tắt phản ánh trạng thái từ server (không lạc quan), nên click rồi chờ kết quả.
  await staff.getByRole("checkbox").first().click();
  await expect(staff.getByText("AI đang bật")).toBeVisible();

  // Khách chào.
  const { widget } = await openWidget(browser);
  await customerSays(widget, "alo shop");

  // Trace: run xuất hiện sau khi hết cửa sổ gom tin; mở lại màn hình để tải danh sách mới.
  const nav = staff.getByRole("navigation", { name: "Màn hình" });
  const run = staff.getByRole("button", { name: /Có nháp/ });
  await expect(async () => {
    await nav.getByRole("button", { name: "Agent" }).click();
    await nav.getByRole("button", { name: "Trace" }).click();
    await expect(run.first()).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 45_000 });

  await run.first().click();
  await expect(staff.getByText("Dạ em chào anh/chị")).toBeVisible();
  await expect(staff.getByText("Có căn cứ", { exact: true })).toBeVisible();
  await expect(staff.getByText("Nháp chỉ hiện ở đây")).toBeVisible();

  // Bất biến an toàn: nháp không xuất hiện ở phía khách.
  await expect(widget.getByRole("log").getByText("Dạ em chào anh/chị")).toHaveCount(0);
});
