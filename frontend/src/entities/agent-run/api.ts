import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import type { RunDetail, RunListItem, RunStatus } from "./trace";

type Page = { items: RunListItem[]; next_cursor: string | null };

export function useRunList(status: RunStatus | "") {
  const query = useInfiniteQuery({
    queryKey: ["agent-runs", status],
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: "30" });
      if (status) q.set("status", status);
      if (pageParam) q.set("cursor", pageParam);
      return apiFetch<Page>(`/api/v1/agent-runs?${q}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 15_000, // run mới xuất hiện khi khách nhắn; không cần realtime ở bước này
  });
  return { ...query, runs: query.data?.pages.flatMap((p) => p.items) ?? [] };
}

export const useRunDetail = (id: string | null) =>
  useQuery({
    queryKey: ["agent-run", id],
    queryFn: () => apiFetch<RunDetail>(`/api/v1/agent-runs/${id}`),
    enabled: id !== null,
  });
