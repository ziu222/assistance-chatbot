import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import { lastSequence, mergeTimeline, type Message } from "./timeline";

export const messageKeys = { timeline: (conversationId: string) => ["messages", conversationId] as const };

type Page = { messages: Message[]; has_more: boolean };

/** Timeline tăng dần: mỗi lần refetch chỉ tải phần sau sequence cuối đã có (thiết kế §10). */
export function useTimeline(conversationId: string) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: messageKeys.timeline(conversationId),
    queryFn: async () => {
      let messages = qc.getQueryData<Message[]>(messageKeys.timeline(conversationId)) ?? [];
      for (;;) {
        const page = await apiFetch<Page>(
          `/api/v1/conversations/${conversationId}/messages?after_sequence=${lastSequence(messages)}&limit=200`,
        );
        messages = mergeTimeline(messages, page.messages);
        if (!page.has_more) return messages;
      }
    },
  });
}
