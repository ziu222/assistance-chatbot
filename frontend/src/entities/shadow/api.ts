import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type Label = "sendable" | "rewrite" | "wrong";
export type Comparison = {
  id: string;
  intent: string | null;
  customer: string;
  draft: string;
  staff: string;
  judge: { verdict: string; sendable: string; label: Label; rationale: string | null };
  label: Label | null;
  created_at: string;
};
export type Calibration = {
  judge_prompt_version: string;
  labels: number;
  kappa: number | null;
  agreement: number | null;
  calibrated: boolean;
  needs: { labels: number; kappa: number };
};

export type IntentSummary = {
  intent: string | null;
  source?: "blind" | "shown";
  models: string[];
  total: number;
  unanswered: number;
  scored: number;
  sendable_rate: number | null;
  wrong_rate: number | null;
  handoff_extra: number;
  handoff_missing: number;
  median_bot_lead_s: number | null;
  ready: boolean;
  not_ready_because: string[];
};

export type LearningIntentSummary = IntentSummary & {
  source: "shown";
  as_is_rate: number | null;
  minor_edit_rate: number | null;
  discarded_rate: number | null;
};

export const useShadowSummary = (days = 14) =>
  useQuery({
    queryKey: ["shadow", "summary", days],
    queryFn: () =>
      apiFetch<{
        days: number;
        judge_calibrated: boolean;
        intents: IntentSummary[];
        learning_intents?: LearningIntentSummary[];
      }>(`/api/v1/shadow/summary?days=${days}`),
  });

export const LABELS: Record<Label, string> = { sendable: "Gửi được", rewrite: "Phải sửa", wrong: "Sai" };

export const useComparisons = () =>
  useQuery({
    queryKey: ["shadow", "comparisons"],
    queryFn: () => apiFetch<{ items: Comparison[] }>("/api/v1/shadow/comparisons"),
  });

export const useCalibration = () =>
  useQuery({ queryKey: ["shadow", "calibration"], queryFn: () => apiFetch<Calibration>("/api/v1/shadow/calibration") });

export function useLabel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, label }: { id: string; label: Label }) =>
      apiFetch<{ ok: boolean }>(`/api/v1/shadow/comparisons/${id}/label`, { method: "POST", body: { label } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["shadow"] }),
  });
}
