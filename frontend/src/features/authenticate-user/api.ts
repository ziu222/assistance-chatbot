import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../../shared/api/client";

export type Me = {
  user: { id: string; email: string; display_name: string };
  tenant: { id: string; name: string; slug: string };
  role: "owner" | "admin" | "agent";
};

export const meKey = ["me"] as const;

/** null = chưa đăng nhập (401), không coi là lỗi. */
export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => {
      try {
        return await apiFetch<Me>("/api/v1/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: Infinity,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string; tenant_slug?: string }) =>
      apiFetch<Me>("/api/v1/auth/login", { method: "POST", body }),
    onSuccess: (me) => qc.setQueryData(meKey, me),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/api/v1/auth/logout", { method: "POST" }),
    onSettled: () => {
      qc.clear(); // không để dữ liệu của tenant cũ lại trong cache
      qc.setQueryData(meKey, null);
    },
  });
}
