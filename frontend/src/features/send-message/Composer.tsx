import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { conversationKeys } from "../../entities/conversation/api";
import { messageKeys } from "../../entities/message/api";
import type { PendingReply } from "../../entities/message/timeline";
import { ApiError, apiFetch } from "../../shared/api/client";

const MAX_RETRIES = 3;
const VND = new Intl.NumberFormat("vi-VN");

type BotDraft = { id: string; text: string; needs_human: boolean; reason: string | null; amounts: number[] };
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
  const [usingDraft, setUsingDraft] = useState<string | null>(null); // nháp đang sửa trong ô soạn
  // Chế độ Learning (thiết kế 05): nháp của bot hiện ở đây; người vẫn là người gửi.
  // Khóa nằm dưới ["conversation", id] nên tín hiệu SSE của hội thoại tự làm mới.
  const draftKey = ["conversation", conversationId, "draft"] as const;
  const botDraft = useQuery({
    queryKey: draftKey,
    queryFn: () => apiFetch<{ draft: BotDraft | null }>(`/api/v1/conversations/${conversationId}/draft`),
  }).data?.draft;

  const patch = (cmid: string, status: PendingReply["status"]) =>
    setPending((list) => list.map((p) => (p.cmid === cmid ? { ...p, status } : p)));

  const deliver = async (cmid: string, text: string, draftId?: string | null) => {
    for (let attempt = 0; ; attempt++) {
      try {
        await apiFetch(`/api/v1/conversations/${conversationId}/messages`, {
          method: "POST",
          body: { client_message_id: cmid, text, ...(draftId ? { draft_id: draftId } : {}) },
        });
        void qc.invalidateQueries({ queryKey: draftKey });
        await qc.invalidateQueries({ queryKey: messageKeys.timeline(conversationId) });
        void qc.invalidateQueries({ queryKey: conversationKeys.all });
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
    void deliver(cmid, text, usingDraft);
    setUsingDraft(null);
  };

  const sendBotDraft = (d: BotDraft) => {
    const cmid = crypto.randomUUID();
    setPending((list) => [...list, { cmid, text: d.text, status: "sending" }]);
    void deliver(cmid, d.text, d.id);
  };

  const discardBotDraft = async (d: BotDraft) => {
    await apiFetch(`/api/v1/conversations/${conversationId}/draft/${d.id}/discard`, { method: "POST" }).catch(() => null);
    void qc.invalidateQueries({ queryKey: draftKey });
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
      {botDraft && usingDraft !== botDraft.id && !disabled && (
        <section className="card" aria-label="Gợi ý của bot">
          <small className="muted">
            Gợi ý của bot{botDraft.needs_human && ` · bot cho rằng cần nhân viên${botDraft.reason ? ` (${botDraft.reason})` : ""}`}
          </small>
          <p className="pre">{botDraft.text}</p>
          {botDraft.amounts.length > 0 && (
            <small className="hint">Kiểm tra số tiền: {botDraft.amounts.map((a) => `${VND.format(a)}đ`).join(", ")}</small>
          )}
          <div className="row">
            <button type="button" onClick={() => sendBotDraft(botDraft)}>
              Dùng
            </button>
            <button
              type="button"
              className="link"
              onClick={() => {
                setDraft(botDraft.text);
                setUsingDraft(botDraft.id);
              }}
            >
              Sửa
            </button>
            <button type="button" className="link" onClick={() => void discardBotDraft(botDraft)}>
              Bỏ
            </button>
          </div>
        </section>
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
