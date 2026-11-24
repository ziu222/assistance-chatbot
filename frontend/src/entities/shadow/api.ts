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
