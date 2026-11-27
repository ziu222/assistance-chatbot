import type { Coverage } from "./api";
import { KNOWLEDGE_TOPICS, type KnowledgeTopicMeta } from "./topics";

/** 4 chủ đề cốt lõi gợi ý hỏi nhanh khi thiết lập shop mới (thiết kế bước 2 §13.3). */
export const ONBOARDING_TOPIC_IDS = ["hours", "shipping", "payment", "returns"] as const;

const CORE_TOPICS: KnowledgeTopicMeta[] = ONBOARDING_TOPIC_IDS.map(
  (id) => KNOWLEDGE_TOPICS.find((t) => t.id === id)!,
);

export type OnboardingFaqPayload = {
  question: string;
  answer: string;
  topics: string[];
  status: "approved";
};

export function missingOnboardingTopics(coverage: Coverage[]): KnowledgeTopicMeta[] {
  const covered = new Set(coverage.filter((c) => c.covered).map((c) => c.topic));
  return CORE_TOPICS.filter((t) => !covered.has(t.id));
}

/** Chỉ tạo FAQ cho những mục chủ shop thật sự nhập nội dung; mục để trống được bỏ qua. */
export function buildOnboardingFaqs(answers: Record<string, string>): OnboardingFaqPayload[] {
  const out: OnboardingFaqPayload[] = [];
  for (const topic of CORE_TOPICS) {
    const answer = (answers[topic.id] ?? "").trim();
    if (!answer) continue;
    out.push({
      question: topic.sampleQuestion,
      answer,
      topics: [topic.id],
      status: "approved",
    });
  }
  return out;
}
