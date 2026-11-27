import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import { lastSequence, mergeTimeline, nextAfterSequence, type Message } from "./timeline";

export const messageKeys = { timeline: (conversationId: string) => ["messages", conversationId] as const };

type Page = { messages: Message[]; has_more: boolean };

/** Timeline tăng dần: mỗi lần refetch chỉ tải phần sau sequence cuối đã có (hoặc từ tin pending). */
export function useTimeline(conversationId: string) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: messageKeys.timeline(conversationId),
    queryFn: async () => {
      let messages = qc.getQueryData<Message[]>(messageKeys.timeline(conversationId)) ?? [];
      let after = nextAfterSequence(messages);
      for (;;) {
        const page = await apiFetch<Page>(
          `/api/v1/conversations/${conversationId}/messages?after_sequence=${after}&limit=200`,
        );
        messages = mergeTimeline(messages, page.messages);
        if (!page.has_more || page.messages.length === 0) return messages;
        after = lastSequence(page.messages);
      }
    },
    refetchInterval: (q) => (q.state.data?.some((m) => m.delivery_status === "pending") ? 5_000 : false),
  });
}
