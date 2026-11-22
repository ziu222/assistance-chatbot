import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import type { Handling } from "../agent-run/trace";

export type Day = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
export type Span = [string, string]; // "08:00", "21:00"; end <= start ⇒ qua nửa đêm
export type BusinessHours = {
  timezone: string;
  weekly: Partial<Record<Day, Span[]>>;
  closed_days: { date: string; note: string }[];
  pause_until: string | null;
};
export type SituationConfig = { enabled: boolean; text: string; next: "bot" | "waiting_human" };
export type PolicyConfig = {
  hours: BusinessHours | null;
  paused: SituationConfig;
  after_hours: SituationConfig;
  welcome: SituationConfig & { gap_hours: number };
  handoff_topics: string[];
};
export type Release = { id: string; number: number; created_at: string; current: boolean; config: PolicyConfig };
export type Simulation = {
  situation: "paused" | "after_hours" | "welcome" | null;
  text: string | null;
  set_handling: Handling | null;
  run_agent: boolean;
};
export type SimulateInput = {
  config: PolicyConfig;
  at: string;
  is_new_conversation: boolean;
  minutes_since_last_message: number | null;
};

const keys = { policy: ["policy"] as const, releases: ["policy-releases"] as const };

export const usePolicy = () =>
  useQuery({
    queryKey: keys.policy,
    queryFn: () => apiFetch<{ release: Release | null; auto_send: boolean; suggested: PolicyConfig }>("/api/v1/policy"),
  });

export const useReleases = () =>
  useQuery({ queryKey: keys.releases, queryFn: () => apiFetch<{ items: Release[] }>("/api/v1/policy/releases") });

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: keys.policy });
    void qc.invalidateQueries({ queryKey: keys.releases });
  };
}

export function useSavePolicy() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (config: PolicyConfig) => apiFetch<Release>("/api/v1/policy", { method: "PUT", body: config }),
    onSuccess: done,
  });
}

export function useAutoSend() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (auto_send: boolean) =>
      apiFetch<{ auto_send: boolean }>("/api/v1/policy/settings", { method: "PATCH", body: { auto_send } }),
    onSettled: done,
  });
}

export function useRestoreRelease() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => apiFetch<Release>(`/api/v1/policy/releases/${id}/restore`, { method: "POST" }),
    onSuccess: done,
  });
}

/** Mô phỏng chạy lại mỗi khi bản nháp hoặc thời điểm đổi; không ghi gì ở server. */
export const useSimulation = (input: SimulateInput) =>
  useQuery({
    queryKey: ["policy-simulate", input],
    queryFn: () => apiFetch<Simulation>("/api/v1/policy/simulate", { method: "POST", body: input }),
    placeholderData: (prev) => prev,
    retry: false,
  });

export function useReturnToBot(conversationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) =>
      apiFetch<{ handling: Handling }>(`/api/v1/conversations/${conversationId}/return-to-bot`, {
        method: "POST",
        body: { version },
      }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}
