import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type Agent = {
  id: string;
  name: string;
  channel_connection_id: string;
  template_key: string;
  template_version: number;
  mode: "manual" | "shadow";
  shadow_sample_rate: number;
  version: number; // optimistic concurrency: gửi lại khi sửa
  current_version: { id: string; version_number: number };
};
export type Template = { key: string; version: number; default_mode: string; intents: Record<string, string> };
export type Connection = { id: string; provider: string; status: string; agent_id: string | null };
export type AiSettings = { ai_enabled: boolean; daily_cost_cap_micro: number };

const keys = {
  agents: ["agents"] as const,
  templates: ["agent-templates"] as const,
  connections: ["channel-connections"] as const,
  ai: ["ai-settings"] as const,
};

export const useAgents = () =>
  useQuery({ queryKey: keys.agents, queryFn: () => apiFetch<{ items: Agent[] }>("/api/v1/agents") });
export const useTemplates = () =>
  useQuery({ queryKey: keys.templates, queryFn: () => apiFetch<{ items: Template[] }>("/api/v1/agent-templates") });
export const useConnections = () =>
  useQuery({
    queryKey: keys.connections,
    queryFn: () => apiFetch<{ items: Connection[] }>("/api/v1/channel-connections"),
  });
export const useAiSettings = () =>
  useQuery({ queryKey: keys.ai, queryFn: () => apiFetch<AiSettings>("/api/v1/ai-settings") });

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { template_key: string; channel_connection_id: string; name: string }) =>
      apiFetch<Agent>("/api/v1/agents", { method: "POST", body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.agents });
      void qc.invalidateQueries({ queryKey: keys.connections });
    },
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; version: number; mode?: Agent["mode"]; shadow_sample_rate?: number }) =>
      apiFetch<Agent>(`/api/v1/agents/${id}`, { method: "PATCH", body }),
    // Thành công hay xung đột phiên bản đều tải lại để nhìn thấy trạng thái thật.
    onSettled: () => qc.invalidateQueries({ queryKey: keys.agents }),
  });
}

export function useUpdateAiSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<AiSettings>) => apiFetch<AiSettings>("/api/v1/ai-settings", { method: "PATCH", body }),
    onSuccess: (data) => qc.setQueryData(keys.ai, data),
  });
}
