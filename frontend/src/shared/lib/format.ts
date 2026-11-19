const time = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" });
const day = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit" });

/** Hôm nay: giờ:phút; ngày khác: ngày/tháng. */
export function formatTime(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toDateString() === now.toDateString() ? time.format(d) : day.format(d);
}
