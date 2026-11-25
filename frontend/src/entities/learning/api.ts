import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type CandidateStatus = "pending" | "approved" | "eval_failed" | "published" | "rejected";

export type Candidate = {
  id: string;
  kind: "faq_new" | "faq_update" | "doc_section" | "brand_example" | "order_flow";
  intent: string;
  topic: string;
  proposal: { question?: string; answer?: string; title?: string; body?: string };
  replaces: string | null;
  current_entry: { id: string; question?: string; answer?: string; title?: string; body?: string } | null;
  evidence: { draft_id: string; conversation_id: string; message_id: string | null }[];
  checks: {
    passed: boolean;
    ungrounded_facts?: Record<string, string[]>;
    pii?: string[];
    customer_overlap?: boolean;
  };
  status: CandidateStatus;
  reject_reason: string | null;
  release_id: string | null;
  eval_report: {
    passed: boolean;
    total: number;
    failed_cases: { case_id: string; question: string; reply: string; reasons: string[] }[];
  } | null;
  created_at: string;
};

export const useCandidates = (status?: CandidateStatus) =>
  useQuery({
    queryKey: ["learning", "candidates", status ?? "all"],
    queryFn: () =>
      apiFetch<{ items: Candidate[] }>(
        status ? `/api/v1/learning/candidates?status=${encodeURIComponent(status)}` : "/api/v1/learning/candidates",
      ),
  });

export function useApproveCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: Record<string, string> }) =>
      apiFetch<Candidate>(`/api/v1/learning/candidates/${id}/approve`, {
        method: "POST",
        body: body ?? {},
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["learning", "candidates"] }),
  });
}

export function useRejectCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      apiFetch<Candidate>(`/api/v1/learning/candidates/${id}/reject`, {
        method: "POST",
        body: reason ? { reason } : {},
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["learning", "candidates"] }),
  });
}
