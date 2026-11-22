import { expect, test } from "@playwright/test";
import { customerSays, openWidget, staffLogin } from "./helpers";

// Bước 2.8 (thiết kế §12.5): khách bấm "Nhờ nhân viên" ⇒ đúng một ticket hiện ở tab "Cần xử lý";
// nhân viên nhận rồi đóng ticket.
test("khách nhờ nhân viên ⇒ ticket hiện ở tab Cần xử lý, nhân viên nhận và đóng", async ({ browser }) => {
  const staff = await staffLogin(browser);
  const { widget } = await openWidget(browser);
  const text = `áo này còn màu be không ${Date.now()}`;
  await customerSays(widget, text);

  const ask = widget.getByRole("button", { name: "Nhờ nhân viên hỗ trợ" });
  await ask.click();
  await expect(widget.getByText("Đã báo nhân viên")).toBeVisible();

  await staff.getByRole("navigation", { name: "Màn hình" }).getByRole("button", { name: /Cần xử lý/ }).click();
  const ticket = staff.getByRole("article").filter({ hasText: text });
  await expect(ticket).toHaveCount(1); // SSE báo ticket mới, không cần tải lại
  await expect(ticket.getByText("Khách bấm nút")).toBeVisible();
  await expect(ticket.getByText("Chưa ai nhận")).toBeVisible();

  await ticket.getByRole("button", { name: "Nhận" }).click();
  await expect(ticket.getByText("Đã có người nhận")).toBeVisible();
  await ticket.getByRole("button", { name: "Xong" }).click();
  await expect(staff.getByRole("article").filter({ hasText: text })).toHaveCount(0);
});
