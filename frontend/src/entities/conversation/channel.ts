export type ChannelProvider = "webchat" | "messenger" | "zalo_oa";

export const CHANNEL_FILTERS: readonly { id: ChannelProvider | null; label: string }[] = [
  { id: null, label: "Tất cả" },
  { id: "webchat", label: "Web" },
  { id: "messenger", label: "Messenger" },
  { id: "zalo_oa", label: "Zalo OA" },
];

const CHANNEL_LABELS: Record<string, string> = {
  webchat: "Web",
  messenger: "Messenger",
  zalo_oa: "Zalo OA",
};

/** Đọc giá trị `provider` hợp lệ từ query param URL. */
export function parseChannelProvider(raw: string | null): ChannelProvider | null {
  if (raw === "webchat" || raw === "messenger" || raw === "zalo_oa") return raw;
  return null;
}

/** Nhãn hiển thị ngắn gọn cho từng kênh bán hàng. */
export function channelLabel(provider: string): string {
  return CHANNEL_LABELS[provider] ?? provider;
}

export type ChannelWindowStatus = "unlimited" | "open" | "expiring" | "expired";

export type ChannelWindowState = {
  status: ChannelWindowStatus;
  remainingMinutes: number | null;
};

const EXPIRING_THRESHOLD_MINUTES = 120;

/** Tính trạng thái cửa sổ trả lời từ mốc `reply_window_expires_at` của server. */
export function channelWindowState(
  expiresAt: string | null | undefined,
  nowMs = Date.now(),
): ChannelWindowState {
  if (!expiresAt) return { status: "unlimited", remainingMinutes: null };
  const expMs = Date.parse(expiresAt);
  if (Number.isNaN(expMs)) return { status: "unlimited", remainingMinutes: null };
  const diffMs = expMs - nowMs;
  if (diffMs <= 0) return { status: "expired", remainingMinutes: 0 };
  const remainingMinutes = Math.ceil(diffMs / 60_000);
  return {
    status: remainingMinutes <= EXPIRING_THRESHOLD_MINUTES ? "expiring" : "open",
    remainingMinutes,
  };
}

/** Định dạng số phút còn lại thành chuỗi tiếng Việt dễ đọc (vd "1 giờ 20 phút", "45 phút"). */
export function formatRemainingWindow(minutes: number): string {
  const safe = Math.max(0, Math.floor(minutes));
  if (safe < 60) return `${safe} phút`;
  const hours = Math.floor(safe / 60);
  const mins = safe % 60;
  return mins > 0 ? `${hours} giờ ${mins} phút` : `${hours} giờ`;
}

const CHANNEL_ERROR_MESSAGES: Record<string, string> = {
  messenger_window_expired: "Đã quá cửa sổ 24 giờ của Messenger kể từ tin cuối của khách.",
  zalo_window_expired: "Đã quá cửa sổ 48 giờ của Zalo OA kể từ tin cuối của khách.",
  zalo_reply_limit: "Đã đạt giới hạn 8 tin phản hồi liên tiếp của Zalo OA.",
  zalo_quota_exhausted: "Kết nối Zalo OA đã hết hạn mức gửi tin.",
  message_too_long: "Tin nhắn vượt quá độ dài tối đa của kênh.",
};

/** Map mã lỗi từ backend kênh sang thông báo tiếng Việt cho nhân viên. */
export function channelErrorMessage(code: string): string | null {
  return CHANNEL_ERROR_MESSAGES[code] ?? null;
}

/** Lỗi kênh mang tính nghiệp vụ/giới hạn: bấm "Gửi lại" cũng không thể thành công. */
export function isNonRetryableChannelError(code: string): boolean {
  return code in CHANNEL_ERROR_MESSAGES || code === "unsupported_provider";
}
