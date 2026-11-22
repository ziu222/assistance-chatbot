import { readFileSync } from "node:fs";
import { expect, type Browser, type BrowserContext, type Frame, type Page } from "@playwright/test";

// Dùng chung cho các spec e2e: dữ liệu seed, widget của khách, đăng nhập nhân viên.
export const seed = JSON.parse(readFileSync(".seed.json", "utf-8")) as {
  email: string;
  password: string;
  publicKey: string;
};

/** Bấm nút nổi đúng MỘT lần (nút là toggle; nằm trong Shadow DOM đóng nên bấm theo tọa độ). */
export async function clickLauncher(page: Page): Promise<Frame> {
  await page.waitForFunction(() => (window as { __atlasWebchat?: boolean }).__atlasWebchat === true);
  const { width, height } = page.viewportSize()!;
  await page.mouse.click(width - 48, height - 48);
  await expect.poll(() => page.frames().some((f) => f.url().includes("/w/"))).toBe(true);
  const widget = page.frames().find((f) => f.url().includes("/w/"))!;
  await widget.getByLabel("Nhập tin nhắn").waitFor();
  return widget;
}

export async function openWidget(browser: Browser): Promise<{ page: Page; widget: Frame }> {
  const page = await (await browser.newContext()).newPage();
  await page.goto(`http://localhost:5500/?key=${seed.publicKey}`);
  return { page, widget: await clickLauncher(page) };
}

// Đăng nhập bằng form một lần mỗi worker rồi dùng lại cookie phiên: backend giới hạn 5 lần
// đăng nhập/phút cho mỗi email (chống dò mật khẩu), mà bộ e2e có nhiều spec cần nhân viên.
let staffState: Awaited<ReturnType<BrowserContext["storageState"]>> | null = null;

export async function staffLogin(browser: Browser): Promise<Page> {
  const context = await browser.newContext(staffState ? { storageState: staffState } : {});
  const page = await context.newPage();
  await page.goto("http://localhost:5173/");
  if (!staffState) {
    await page.getByLabel("Email").fill(seed.email);
    await page.getByLabel("Mật khẩu").fill(seed.password);
    await page.getByRole("button", { name: "Đăng nhập" }).click();
  }
  await expect(page.getByRole("status").filter({ hasText: "Trực tuyến" })).toBeVisible();
  staffState ??= await context.storageState();
  return page;
}

export async function customerSays(widget: Frame, text: string) {
  await widget.getByLabel("Nhập tin nhắn").fill(text);
  // Chờ phiên sẵn sàng: trước đó nút Gửi bị tắt và Enter bị bỏ qua (đúng hành vi).
  await expect(widget.getByRole("button", { name: "Gửi" })).toBeEnabled();
  await widget.getByLabel("Nhập tin nhắn").press("Enter");
  await expect(widget.getByRole("log").getByText(text)).toBeVisible();
}
