import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type AutopilotIntentItem = {
  intent: string;
  state: "autopilot" | "learning";
  config_key: Record<string, unknown> | null;
  source: "blind" | "shown" | null;
  enabled_at: string | null;
  demoted_at: string | null;
  demote_reason: string | null;
  can_enable: boolean;
  enable_source: "blind" | "shown" | null;
  enable_reasons: string[];
  blind_ready: boolean;
  shown_ready: boolean;
};

export type AutopilotIntentsResponse = {
  agent_id: string | null;
  ai_enabled: boolean;
  auto_send: boolean;
  max_bot_streak: number;
  items: AutopilotIntentItem[];
};

export const useAutopilotIntents = (days = 14) =>
  useQuery({
    queryKey: ["autopilot", "intents", days],
    queryFn: () => apiFetch<AutopilotIntentsResponse>(`/api/v1/autopilot/intents?days=${days}`),
  });

export function useEnableAutopilot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ intent, source }: { intent: string; source?: "blind" | "shown" }) =>
      apiFetch(`/api/v1/autopilot/intents/${encodeURIComponent(intent)}/enable`, {
        method: "POST",
        body: source ? { source } : {},
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["autopilot"] }),
  });
}

export function useDisableAutopilot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (intent: string) =>
      apiFetch(`/api/v1/autopilot/intents/${encodeURIComponent(intent)}/disable`, {
        method: "POST",
        body: {},
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["autopilot"] }),
  });
}

export function usePauseAutopilot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<{ auto_send: boolean }>("/api/v1/autopilot/pause", { method: "POST", body: {} }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["autopilot"] });
      void qc.invalidateQueries({ queryKey: ["policy"] });
    },
  });
}

export type OwnerReplyLabel = "correct" | "minor_edit" | "wrong";

export type AutopilotReply = {
  message_id: string;
  conversation_id: string;
  agent_id: string;
  intent: string;
  customer_text: string;
  bot_text: string;
  occurred_at: string;
  followups: { kind: string; detail: Record<string, unknown>; created_at: string }[];
  owner_label: { label: OwnerReplyLabel; wrong_fact?: boolean } | null;
};

export const useAutopilotReplies = (days = 7) =>
  useQuery({
    queryKey: ["autopilot", "replies", days],
    queryFn: () => apiFetch<{ days: number; items: AutopilotReply[] }>(`/api/v1/autopilot/replies?days=${days}`),
  });

export function useLabelAutopilotReply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      messageId,
      label,
      wrongFact = false,
    }: {
      messageId: string;
      label: OwnerReplyLabel;
      wrongFact?: boolean;
    }) =>
      apiFetch(`/api/v1/autopilot/replies/${messageId}/label`, {
        method: "POST",
        body: { label, wrong_fact: wrongFact },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["autopilot"] }),
  });
}
