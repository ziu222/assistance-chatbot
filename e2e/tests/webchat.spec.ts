import { expect, test } from "@playwright/test";
import { customerSays, openWidget, seed, staffLogin, clickLauncher } from "./helpers";

// Tiêu chí chấp nhận bước 1 (thiết kế §16): hai chiều realtime, XSS hiển thị text, mất SSE không
// mất tin, reload widget giữ lịch sử.
test("tin khách hiện ở inbox và trả lời của nhân viên tới widget, không cần reload", async ({ browser }) => {
  const staff = await staffLogin(browser);
  const { widget } = await openWidget(browser);
  const question = `Áo size M còn không? ${Date.now()}`;

  await customerSays(widget, question);

  const row = staff.getByRole("navigation", { name: "Danh sách hội thoại" }).getByRole("button", {
    name: new RegExp(question.replace(/[?]/g, "\\?")),
  });
  await expect(row).toBeVisible(); // realtime: không reload trang inbox
  await expect(row.getByText("1", { exact: true })).toBeVisible(); // badge chưa đọc
  await row.click();

  const reply = `Dạ còn ạ ${Date.now()}`;
  await staff.getByLabel("Trả lời khách").fill(reply);
  await staff.getByLabel("Trả lời khách").press("Enter");

  await expect(widget.getByRole("log").getByText(reply)).toBeVisible(); // realtime phía khách
  await expect(row.getByText("1", { exact: true })).toHaveCount(0); // mở hội thoại => đã đọc
});

test("payload XSS hiển thị nguyên văn ở cả widget và inbox", async ({ browser }) => {
  const staff = await staffLogin(browser);
  const { page, widget } = await openWidget(browser);
  let dialog = false;
  page.on("dialog", () => (dialog = true));
  staff.on("dialog", () => (dialog = true));
  const payload = `<img src=x onerror=alert(1)> ${Date.now()}`;

  await customerSays(widget, payload);
  await staff.getByRole("button", { name: new RegExp(String(payload.split(" ").pop())) }).click();

  await expect(staff.getByRole("log").getByText(payload)).toBeVisible();
  await expect(staff.locator("img[src='x']")).toHaveCount(0);
  expect(dialog).toBe(false);
});

test("inbox mất SSE rồi nối lại không mất tin", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  // Chặn SSE thật (setOffline không cắt kết nối localhost đang mở).
  await page.route("**/api/v1/events", (route) => route.abort());
  await page.goto("http://localhost:5173/");
  await page.getByLabel("Email").fill(seed.email);
  await page.getByLabel("Mật khẩu").fill(seed.password);
  await page.getByRole("button", { name: "Đăng nhập" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Đang kết nối lại" })).toBeVisible();

  const { widget } = await openWidget(browser);
  const missed = `Tin gửi lúc inbox mất SSE ${Date.now()}`;
  await customerSays(widget, missed); // tín hiệu của tin này bị lỡ

  await page.unroute("**/api/v1/events");
  await expect(page.getByRole("status").filter({ hasText: "Trực tuyến" })).toBeVisible();
  await expect(page.getByText(missed)).toBeVisible(); // kết nối lại => refetch REST
});

test("tải lại trang host vẫn giữ lịch sử của khách", async ({ browser }) => {
  const { page, widget } = await openWidget(browser);
  const text = `Nhớ tôi không? ${Date.now()}`;
  await customerSays(widget, text);

  await page.reload();
  const again = await clickLauncher(page);
  await expect(again.getByRole("log").getByText(text)).toBeVisible();
});
