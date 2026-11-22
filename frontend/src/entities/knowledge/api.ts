import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type Kind = "faq" | "doc";
export type Entry = { id: string; status: "draft" | "approved" | "archived"; updated_at: string } & (
  | { question: string; answer: string }
  | { title: string; body: string }
);
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
export type ImportResult = { items: number; created: number; updated: number };

const PATH: Record<Kind, string> = { faq: "/api/v1/knowledge/faqs", doc: "/api/v1/knowledge/docs" };
const key = (kind: Kind) => ["knowledge", kind] as const;

export const useEntries = <T extends Entry>(kind: Kind) =>
  useQuery({ queryKey: key(kind), queryFn: () => apiFetch<{ items: T[] }>(PATH[kind]) });

export function useCreateEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, string>) => apiFetch<Entry>(PATH[kind], { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(kind) }),
  });
}

export function useUpdateEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, string>) =>
      apiFetch<Entry>(`${PATH[kind]}/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(kind) }),
  });
}

export function useArchiveEntry(kind: Kind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`${PATH[kind]}/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(kind) }),
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
