import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

// Migrate rồi seed một tenant mới cho mỗi lần chạy (không đụng dữ liệu cũ).
// Mật khẩu chỉ ghi vào e2e/.seed.json (đã .gitignore), không in ra log.
export default function globalSetup() {
  const run = (args: string[]) =>
    execFileSync("uv", ["run", ...args], {
      cwd: "../backend",
      encoding: "utf-8",
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

  run(["alembic", "upgrade", "head"]);
  // Mỗi lần chạy độc lập: xóa bộ đếm rate limit (chỉ khóa rl:*), nếu không chạy liền 2 lần
  // sẽ vượt giới hạn sessions/IP/phút.
  run([
    "python", "-c",
    "import redis; from app.config.settings import get_settings as g; r = redis.from_url(g().redis_url); " +
      "[r.delete(k) for k in r.scan_iter('rl:*')]",
  ]);
  const email = `e2e-${Date.now()}@shop.test`;
  const out = run([
    "python", "-m", "app.entrypoints.cli", "seed",
    "--tenant", "Shop E2E", "--email", email, "--origin", "http://localhost:5500",
  ]);
  const field = (name: string) => out.match(new RegExp(`^${name}\\s*:\\s*(\\S+)`, "m"))?.[1];
  const seed = { email, password: field("password"), publicKey: field("public_key") };
  if (!seed.password || !seed.publicKey) throw new Error("seed output không đúng định dạng");
  writeFileSync(".seed.json", JSON.stringify(seed));
}
