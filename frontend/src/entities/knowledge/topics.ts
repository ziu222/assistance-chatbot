/** Danh sách chủ đề kiến thức khớp 1-1 với backend `knowledge/domain/topics.py`. */

export type KnowledgeTopicMeta = {
  id: string;
  label: string;
  sampleQuestion: string;
  placeholderAnswer: string;
};

export const KNOWLEDGE_TOPICS: readonly KnowledgeTopicMeta[] = [
  {
    id: "hours",
    label: "giờ làm việc",
    sampleQuestion: "Shop mở cửa mấy giờ?",
    placeholderAnswer: "Vd: Shop làm việc từ 8:00 đến 21:00 từ thứ Hai đến thứ Bảy.",
  },
  {
    id: "shipping",
    label: "phí giao hàng",
    sampleQuestion: "Phí giao hàng và thời gian nhận hàng thế nào?",
    placeholderAnswer: "Vd: Nội thành 20.000đ (1-2 ngày), đi tỉnh 30.000đ (2-4 ngày).",
  },
  {
    id: "payment",
    label: "thanh toán và COD",
    sampleQuestion: "Shop nhận những hình thức thanh toán nào?",
    placeholderAnswer: "Vd: Shop nhận thanh toán khi nhận hàng (COD) và chuyển khoản trước.",
  },
  {
    id: "returns",
    label: "đổi trả",
    sampleQuestion: "Chính sách đổi trả của shop thế nào?",
    placeholderAnswer: "Vd: Hỗ trợ đổi size trong 7 ngày nếu sản phẩm còn nguyên tem mác.",
  },
  {
    id: "warranty",
    label: "bảo hành",
    sampleQuestion: "Sản phẩm được bảo hành bao lâu?",
    placeholderAnswer: "Vd: Bảo hành 6 tháng đối với lỗi từ nhà sản xuất.",
  },
  {
    id: "wholesale",
    label: "mua sỉ",
    sampleQuestion: "Shop có bán sỉ không?",
    placeholderAnswer: "Vd: Đơn từ 10 sản phẩm trở lên có giá sỉ, nhân viên sẽ báo giá riêng.",
  },
  {
    id: "promo",
    label: "khuyến mãi",
    sampleQuestion: "Shop hiện có chương trình khuyến mãi nào không?",
    placeholderAnswer: "Vd: Giảm 10% cho đơn từ 500.000đ trong tháng này.",
  },
  {
    id: "invoice",
    label: "hóa đơn",
    sampleQuestion: "Shop có xuất hóa đơn VAT không?",
    placeholderAnswer: "Vd: Shop có xuất hóa đơn VAT điện tử khi khách cung cấp mã số thuế.",
  },
];

const BY_ID = new Map(KNOWLEDGE_TOPICS.map((t) => [t.id, t]));

export function topicLabel(id: string): string {
  return BY_ID.get(id)?.label ?? id;
}

export type GapDraftSeed = {
  key: number;
  question: string;
  topics: string[] | null;
  hint: string;
};

/** Dựng sẵn dữ liệu khởi tạo cho form Thêm FAQ khi chủ shop bấm "Viết câu trả lời" từ lỗ hổng tri thức. */
export function gapDraftTemplate(topic: string, key = Date.now()): GapDraftSeed {
  const meta = BY_ID.get(topic);
  if (!meta) {
    return { key, question: "", topics: null, hint: "Nhập ý chính câu trả lời rồi bấm Gợi ý để trau chuốt theo giọng shop." };
  }
  return {
    key,
    question: meta.sampleQuestion,
    topics: [meta.id],
    hint: meta.placeholderAnswer,
  };
}
