import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { conversationKeys } from "../../entities/conversation/api";
import { apiFetch } from "../../shared/api/client";

/** Đang mở hội thoại có tin chưa đọc (và tab đang hiển thị) => đánh dấu đã đọc. */
export function useMarkRead(conversationId: string, unread: number | undefined) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!unread || document.visibilityState !== "visible") return;
    void apiFetch(`/api/v1/conversations/${conversationId}/read`, { method: "POST" })
      .then(() =>
        Promise.all([
          qc.invalidateQueries({ queryKey: conversationKeys.all }),
          qc.invalidateQueries({ queryKey: conversationKeys.detail(conversationId) }),
        ]),
      )
      .catch(() => {}); // lần tín hiệu sau sẽ thử lại
  }, [conversationId, unread, qc]);
}
