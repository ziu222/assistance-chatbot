import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "../api/client";

const POLL_MS = 30_000; // dự phòng khi SSE chết hẳn

/**
 * SSE là tín hiệu, REST là sự thật (thiết kế §7.4): mỗi tín hiệu chỉ invalidate query liên quan.
 * Cùng origin + cookie => dùng EventSource (tự kết nối lại theo `retry:` của server).
 */
export function useEventSignals(onUnauthorized: () => void): boolean {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);

  useEffect(() => {
    // Polling chỉ chạy khi không có SSE; component render lại sẽ nhận option mới.
    qc.setDefaultOptions({ queries: { refetchInterval: live ? false : POLL_MS } });
  }, [qc, live]);

  useEffect(() => {
    const es = new EventSource("/api/v1/events");
    const refetchAll = () => void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" });

    es.onopen = () => {
      setLive(true);
      refetchAll(); // kết nối (lại): có thể đã lỡ tín hiệu
    };
    es.addEventListener("signal", (e) => {
      try {
        const s = JSON.parse((e as MessageEvent).data) as { t: string; c?: string };
        void qc.invalidateQueries({ queryKey: ["conversations"] });
        void qc.invalidateQueries({ queryKey: ["handoffs"] }); // tin mới có thể đổi hàng đợi
        if (s.t.startsWith("order_request")) void qc.invalidateQueries({ queryKey: ["order-requests"] });
        if (s.c) {
          void qc.invalidateQueries({ queryKey: ["messages", s.c] });
          void qc.invalidateQueries({ queryKey: ["conversation", s.c] });
        }
      } catch {
        refetchAll();
      }
    });
    es.addEventListener("resync", refetchAll);
    es.onerror = () => {
      setLive(false);
      if (es.readyState === EventSource.CLOSED) {
        // Server từ chối hẳn (thường là 401): kiểm tra phiên, hết hạn thì về màn đăng nhập.
        apiFetch("/api/v1/auth/me").catch((err) => {
          if (err instanceof ApiError && err.status === 401) onUnauthorized();
        });
      }
    };
    const onVisible = () => document.visibilityState === "visible" && refetchAll();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      es.close();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [qc, onUnauthorized]);

  return live;
}
