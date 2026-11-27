import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type Kind = "faq" | "doc";
export type Entry = {
  id: string;
  status: "draft" | "approved" | "archived";
  topics: string[] | null;
  effective_topics: string[];
  updated_at: string;
} & ({ question: string; answer: string } | { title: string; body: string });
export type FaqEntry = Extract<Entry, { question: string }>;
export type DocEntry = Extract<Entry, { title: string }>;
export type Product = {
  id: string;
  sku: string;
  name: string;
  description?: string | null;
  price_vnd: number;
  variants: { size?: string; color?: string; stock?: number }[];
};
export type Coverage = { topic: string; label: string; covered: boolean; sources: { type: Kind; id: string; title: string }[] };
export type Gap = { topic: string; label: string; count: number };
export type ImportResult = { items: number; created: number; updated: number };
export type KnowledgeRelease = {
  id: string;
  number: number;
  reason: "manual_edit" | "lesson";
  is_current: boolean;
  faq_count: number;
  doc_count: number;
  knowledge_hash: string;
  created_by: string | null;
  eval_run_id: string | null;
  created_at: string;
};

const PATH: Record<Kind, string> = { faq: "/api/v1/knowledge/faqs", doc: "/api/v1/knowledge/docs" };
const key = (kind: Kind) => ["knowledge", kind] as const;

export const useEntries = <T extends Entry>(kind: Kind) =>
  useQuery({ queryKey: key(kind), queryFn: () => apiFetch<{ items: T[] }>(PATH[kind]) });

export function useCreateEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => apiFetch<Entry>(PATH[kind], { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }), // độ phủ đổi theo
  });
}

export function useUpdateEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      apiFetch<Entry>(`${PATH[kind]}/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }), // độ phủ đổi theo
  });
}

export function useArchiveEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`${PATH[kind]}/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }), // độ phủ đổi theo
  });
}

export function useImportCatalog() {
  return useMutation({
    mutationFn: (csv: string) => apiFetch<ImportResult>("/api/v1/catalog/import", { method: "POST", csv }),
  });
}

/** Thử tìm như tool `search_products` của agent. Chỉ chạy khi có từ khóa. */
export const useCatalogSearch = (q: string) =>
  useQuery({
    queryKey: ["catalog-search", q],
    queryFn: () => apiFetch<{ items: Product[] }>(`/api/v1/catalog/search?q=${encodeURIComponent(q)}`),
    enabled: q.trim().length > 0,
  });

export const useProduct = (id: string | null) =>
  useQuery({
    queryKey: ["catalog-item", id],
    queryFn: () => apiFetch<Product>(`/api/v1/catalog/items/${id}`),
    enabled: id !== null,
  });

/** Chủ đề nào shop đã viết (FAQ/tài liệu đã duyệt), chủ đề nào chưa (§13.3). */
export const useCoverage = () =>
  useQuery({
    queryKey: ["knowledge", "coverage"],
    queryFn: () => apiFetch<{ items: Coverage[] }>("/api/v1/knowledge/coverage"),
  });

/** Số lần bot phải nhờ nhân viên vì thiếu nguồn, theo chủ đề. */
export const useGaps = (days = 7) =>
  useQuery({
    queryKey: ["knowledge", "gaps", days],
    queryFn: () => apiFetch<{ days: number; items: Gap[] }>(`/api/v1/knowledge/gaps?days=${days}`),
  });

/** Lịch sử bản phát hành tri thức có phiên bản (thiết kế 05 L2). */
export const useKnowledgeReleases = () =>
  useQuery({
    queryKey: ["knowledge", "releases"],
    queryFn: () => apiFetch<{ items: KnowledgeRelease[] }>("/api/v1/knowledge/releases"),
  });

export function useRestoreKnowledgeRelease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (number: number) =>
      apiFetch<KnowledgeRelease>(`/api/v1/knowledge/releases/${number}/restore`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}
