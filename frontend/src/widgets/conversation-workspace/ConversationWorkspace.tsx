import { useLayoutEffect, useRef, useState } from "react";
import { useConversation } from "../../entities/conversation/api";
import { useTimeline } from "../../entities/message/api";
import { visiblePending, type PendingReply } from "../../entities/message/timeline";
import { useMarkRead } from "../../features/mark-conversation-read/useMarkRead";
import { Composer } from "../../features/send-message/Composer";
import { formatTime } from "../../shared/lib/format";

export function ConversationWorkspace({ conversationId }: { conversationId: string }) {
  const conversation = useConversation(conversationId);
  const timeline = useTimeline(conversationId);
  const [pending, setPending] = useState<PendingReply[]>([]);
  const logRef = useRef<HTMLOListElement>(null);
  const atBottom = useRef(true);

  useMarkRead(conversationId, conversation.data?.unread_count);

  const messages = timeline.data ?? [];
  const shownPending = visiblePending(pending, messages);

  // Chỉ tự cuộn khi nhân viên đang ở đáy.
  useLayoutEffect(() => {
    const el = logRef.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, shownPending.length]);

  if (conversation.isError || timeline.isError)
    return (
      <section className="workspace">
        <p className="state error" role="alert">
          Không tải được hội thoại.
        </p>
      </section>
    );
  if (!conversation.data || !timeline.data)
    return (
      <section className="workspace">
        <p className="state">Đang tải…</p>
      </section>
    );

  const c = conversation.data;
  return (
    <section className="workspace" aria-label={`Hội thoại với ${c.customer.display_name}`}>
      <header className="ws-head">
        <h2>{c.customer.display_name}</h2>
        <span className={`status ${c.status}`}>{c.status === "open" ? "Đang mở" : "Đã đóng"}</span>
        <span className="channel">{c.connection.provider}</span>
      </header>
      <ol
        ref={logRef}
        className="timeline"
        role="log"
        aria-live="polite"
        aria-label="Tin nhắn"
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {messages.map((m) => (
          <li key={m.id} className={`bubble ${m.direction === "inbound" ? "in" : "out"}`}>
            <span className="sr-only">{m.direction === "inbound" ? "Khách" : "Nhân viên"}: </span>
            {/* chỉ text: React escape; không HTML/markdown (chống stored XSS) */}
            <span className="text">{m.text}</span>
            <time dateTime={m.occurred_at}>{formatTime(m.occurred_at)}</time>
          </li>
        ))}
        {shownPending.map((p) => (
          <li key={p.cmid} className={`bubble out ${p.status}`}>
            <span className="text">{p.text}</span>
            <span className="meta">{p.status === "sending" ? "Đang gửi…" : "Chưa gửi được"}</span>
          </li>
        ))}
      </ol>
      <Composer
        key={conversationId}
        conversationId={conversationId}
        disabled={c.status !== "open"}
        pending={pending}
        setPending={setPending}
      />
    </section>
  );
}
