import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type Level = "fix" | "polish" | "brand" | "alternatives";
export type Kind = "auto_message" | "faq" | "policy" | "other";
export type FactCheck = {
  ok: boolean;
  missing: Partial<Record<FactKind, string[]>>;
  added: Partial<Record<FactKind, string[]>>;
  new_promises: string[];
};
export type FactKind = "amounts" | "durations" | "times" | "phones" | "links" | "variables";
export type Suggestion = { text: string; changes: string; fact_check: FactCheck };
export type WritingRequest = {
  id: string;
  status: "pending" | "running" | "done" | "failed";
  suggestions: Suggestion[];
  error_code: string | null;
};
export type BrandProfile = {
  shop_pronoun: "em" | "shop" | "mình" | "chúng tôi";
  customer_pronoun: "anh/chị" | "bạn" | "chị" | "anh" | "quý khách";
  tone: "than_thien" | "lich_su" | "tre_trung" | "chuyen_nghiep";
  emoji: boolean;
  preferred_terms: string[];
  banned_terms: string[];
  samples: string[];
};

export function useSuggest() {
  return useMutation({
    mutationFn: (body: { kind: Kind; level: Level; text: string }) =>
      apiFetch<{ id: string }>("/api/v1/writing/suggest", { method: "POST", body }),
  });
}

/** Hỏi lại tới khi worker xử lý xong (gợi ý chạy bất đồng bộ, thường vài giây). */
export const useWritingRequest = (id: string | null) =>
  useQuery({
    queryKey: ["writing-request", id],
    queryFn: () => apiFetch<WritingRequest>(`/api/v1/writing/requests/${id}`),
    enabled: id !== null,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 1500),
  });

export function useWritingFeedback(id: string) {
  return useMutation({
    mutationFn: (body: { index: number; action: "used" | "dismissed" }) =>
      apiFetch<{ ok: boolean }>(`/api/v1/writing/requests/${id}/feedback`, { method: "POST", body }),
  });
}

export const useBrandProfile = () =>
  useQuery({
    queryKey: ["brand-profile"],
    queryFn: () => apiFetch<{ number: number | null; profile: BrandProfile }>("/api/v1/brand-profile"),
  });

export function useSaveBrandProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profile: BrandProfile) =>
      apiFetch<{ number: number; profile: BrandProfile }>("/api/v1/brand-profile", { method: "PUT", body: profile }),
    onSuccess: (data) => qc.setQueryData(["brand-profile"], data),
  });
}
