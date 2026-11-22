const vi = {
  title: "Hỗ trợ trực tuyến",
  close: "Đóng cửa sổ chat",
  log: "Tin nhắn",
  inputLabel: "Nhập tin nhắn",
  placeholder: "Nhập tin nhắn…",
  send: "Gửi",
  sending: "Đang gửi…",
  sent: "Đã gửi",
  failed: "Gửi không được.",
  retry: "Gửi lại",
  you: "Bạn",
  staff: "Nhân viên",
  auto: "Tin tự động",
  reconnecting: "Đang kết nối lại…",
  rateLimited: (s: number) => `Bạn gửi hơi nhanh, thử lại sau ${s} giây.`,
  sessionError: "Không mở được phiên chat. Vui lòng tải lại trang.",
  privacy: "Quyền riêng tư",
  defaultGreeting: "Xin chào! Chúng tôi có thể giúp gì cho bạn?",
  askStaff: "Nhờ nhân viên hỗ trợ",
  askStaffDone: "Đã báo nhân viên. Nhân viên sẽ trả lời bạn sớm nhất có thể.",
  askStaffFailed: "Chưa báo được nhân viên, thử lại sau.",
};

type Strings = typeof vi;

const en: Strings = {
  title: "Live support",
  close: "Close chat",
  log: "Messages",
  inputLabel: "Type a message",
  placeholder: "Type a message…",
  send: "Send",
  sending: "Sending…",
  sent: "Sent",
  failed: "Not sent.",
  retry: "Retry",
  you: "You",
  staff: "Agent",
  auto: "Automatic message",
  reconnecting: "Reconnecting…",
  rateLimited: (s: number) => `You're sending too fast, try again in ${s}s.`,
  sessionError: "Could not start the chat. Please reload the page.",
  privacy: "Privacy",
  defaultGreeting: "Hello! How can we help you?",
  askStaff: "Talk to a person",
  askStaffDone: "We've let our team know. Someone will reply as soon as possible.",
  askStaffFailed: "Couldn't reach the team, please try again later.",
};

export const lang: "vi" | "en" = navigator.language?.toLowerCase().startsWith("en") ? "en" : "vi";
export const t: Strings = lang === "en" ? en : vi;
