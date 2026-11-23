import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

export type OrderItem = {
  sku: string;
  name: string;
  variant: string | null;
  quantity: number;
  unit_price_vnd: number;
  stock_now?: number | null;
};
export type OrderRequest = {
  id: string;
  conversation_id: string;
  status: "collecting" | "ready" | "confirmed" | "cancelled" | "expired";
  channel: string;
  destination: "staff_confirms" | "product_link" | "marketplace_link" | "website_checkout";
  customer_name: string | null;
  items: OrderItem[];
  subtotal_vnd: number;
  customer: Partial<Record<"name" | "phone" | "address" | "delivery_time" | "note", string>>;
  unverified: string[];
  missing: string[];
  opened_at: string;
};

export const FIELD_LABELS: Record<string, string> = {
  product: "sản phẩm",
  variant: "size/màu",
  quantity: "số lượng",
  name: "tên",
  phone: "SĐT",
  address: "địa chỉ",
  delivery_time: "giờ nhận",
  note: "ghi chú",
};

const key = ["order-requests"] as const;

export const useOrderRequests = () =>
  useQuery({ queryKey: key, queryFn: () => apiFetch<{ items: OrderRequest[] }>("/api/v1/order-requests") });

/** Chi tiết (số điện thoại đầy đủ, tồn kho hiện tại); chỉ tải khi nhân viên mở phiếu. */
export const useOrderRequest = (id: string | null) =>
  useQuery({
    queryKey: [...key, id],
    queryFn: () => apiFetch<OrderRequest>(`/api/v1/order-requests/${id}`),
    enabled: id !== null,
  });

export function useOrderAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "confirm" | "cancel" }) =>
      apiFetch<{ ok: boolean }>(`/api/v1/order-requests/${id}/${action}`, { method: "POST" }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: key });
      void qc.invalidateQueries({ queryKey: ["handoffs"] });
    },
  });
}
