import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import type { Handling } from "../agent-run/trace";

export type Ticket = {
  id: string;
  conversation_id: string;
  status: "open" | "claimed";
  source: "customer_button" | "phrase" | "topic" | "staff";
  reason: string | null;
  priority: number;
  opened_at: string;
  claimed_by: string | null;
  customer_name: string | null;
  last_message_preview: string | null;
  handling: Handling;
};
export type TicketAction = "claim" | "resolve" | "dismiss";

export const handoffKeys = { list: ["handoffs"] as const };

export const useHandoffs = () =>
  useQuery({ queryKey: handoffKeys.list, queryFn: () => apiFetch<{ items: Ticket[] }>("/api/v1/handoffs") });

export function useHandoffAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: TicketAction }) =>
      apiFetch<{ ok: boolean }>(`/api/v1/handoffs/${id}/${action}`, { method: "POST" }),
    // Thành công hay xung đột (người khác vừa xử lý) đều tải lại để thấy trạng thái thật.
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: handoffKeys.list });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      void qc.invalidateQueries({ queryKey: ["conversation"] });
    },
  });
}

export function useOpenHandoff(conversationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<{ id: string; created: boolean }>(`/api/v1/conversations/${conversationId}/handoff`, { method: "POST" }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: handoffKeys.list });
      void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
    },
  });
}

const SOURCE: Record<Ticket["source"], string> = {
  customer_button: "Khách bấm nút",
  phrase: "Khách đòi gặp người",
  topic: "Chủ đề cần nhân viên",
  staff: "Nhân viên đánh dấu",
};

export function sourceLabel(t: Pick<Ticket, "source" | "reason">): string {
  return t.source === "topic" && t.reason ? `${SOURCE.topic}: ${t.reason}` : SOURCE[t.source];
}

/** "vừa xong", "chờ 12 phút", "chờ 2 giờ 5 phút". */
export function waitingLabel(openedAt: string, now = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(openedAt).getTime()) / 60_000));
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `chờ ${minutes} phút`;
  const h = Math.floor(minutes / 60);
  return minutes % 60 ? `chờ ${h} giờ ${minutes % 60} phút` : `chờ ${h} giờ`;
}
