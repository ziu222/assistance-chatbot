import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import type { ChannelProvider } from "./channel";
import { flattenPages, type Conversation, type ConversationPage } from "./list";

export const conversationKeys = {
  all: ["conversations"] as const,
  list: (provider?: ChannelProvider | null) => ["conversations", provider ?? "all"] as const,
  detail: (id: string) => ["conversation", id] as const,
};

export function useConversationList(provider?: ChannelProvider | null) {
  const query = useInfiniteQuery({
    queryKey: conversationKeys.list(provider),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "30" });
      if (provider) params.set("provider", provider);
      if (pageParam) params.set("cursor", pageParam);
      return apiFetch<ConversationPage>(`/api/v1/conversations?${params.toString()}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  return { ...query, conversations: query.data ? flattenPages(query.data.pages) : [] };
}

export function useConversation(id: string) {
  return useQuery({
    queryKey: conversationKeys.detail(id),
    queryFn: () => apiFetch<Conversation>(`/api/v1/conversations/${id}`),
  });
}
