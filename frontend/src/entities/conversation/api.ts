import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import { flattenPages, type Conversation, type ConversationPage } from "./list";

export const conversationKeys = {
  list: ["conversations"] as const,
  detail: (id: string) => ["conversation", id] as const,
};

export function useConversationList() {
  const query = useInfiniteQuery({
    queryKey: conversationKeys.list,
    queryFn: ({ pageParam }) =>
      apiFetch<ConversationPage>(
        `/api/v1/conversations?limit=30${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
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
