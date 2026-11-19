import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { conversationKeys } from "../../entities/conversation/api";
import { messageKeys } from "../../entities/message/api";
import type { PendingReply } from "../../entities/message/timeline";
import { ApiError, apiFetch } from "../../shared/api/client";

const MAX_RETRIES = 3;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Props = {
  conversationId: string;
  disabled: boolean;
  pending: PendingReply[];
  setPending: (update: (p: PendingReply[]) => PendingReply[]) => void;
};

/** Enter gửi, Shift+Enter xuống dòng; hiển thị lạc quan; gửi lại dùng CÙNG client_message_id. */
export function Composer({ conversationId, disabled, pending, setPending }: Props) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const patch = (cmid: string, status: PendingReply["status"]) =>
    setPending((list) => list.map((p) => (p.cmid === cmid ? { ...p, status } : p)));

  const deliver = async (cmid: string, text: string) => {
    for (let attempt = 0; ; attempt++) {
      try {
        await apiFetch(`/api/v1/conversations/${conversationId}/messages`, {
          method: "POST",
          body: { client_message_id: cmid, text },
        });
        await qc.invalidateQueries({ queryKey: messageKeys.timeline(conversationId) });
        void qc.invalidateQueries({ queryKey: conversationKeys.list });
        setError(null);
        return;
      } catch (e) {
        const transient = !(e instanceof ApiError) || e.status >= 500;
        if (transient && attempt < MAX_RETRIES) {
          await sleep(500 * 2 ** attempt * (0.5 + Math.random() / 2));
          continue;
        }
        patch(cmid, "failed");
        if (e instanceof ApiError && e.code === "conversation_closed") setError("Hội thoại đã đóng.");
        else if (e instanceof ApiError && e.status === 401) setError("Phiên đăng nhập đã hết hạn.");
        return;
      }
    }
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || disabled) return;
    const cmid = crypto.randomUUID();
    setPending((list) => [...list, { cmid, text, status: "sending" }]);
    setDraft("");
    void deliver(cmid, text);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit();
  };

  return (
    <form className="composer" onSubmit={submit}>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pending.some((p) => p.status === "failed") && (
        <p className="hint">
          Có tin chưa gửi được.{" "}
          {pending
            .filter((p) => p.status === "failed")
            .map((p) => (
              <button
                key={p.cmid}
                type="button"
                className="link"
                onClick={() => {
                  patch(p.cmid, "sending");
                  void deliver(p.cmid, p.text);
                }}
              >
                Gửi lại “{p.text.slice(0, 20)}”
              </button>
            ))}
        </p>
      )}
      <label htmlFor="reply" className="sr-only">
        Trả lời khách
      </label>
      <textarea
        id="reply"
        placeholder={disabled ? "Hội thoại đã đóng" : "Nhập trả lời… (Enter để gửi, Shift+Enter xuống dòng)"}
        maxLength={4000}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" disabled={disabled || !draft.trim()}>
        Gửi
      </button>
    </form>
  );
}
