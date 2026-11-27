import {
  priorityBadge,
  sourceLabel,
  useHandoffAction,
  useHandoffs,
  waitingLabel,
  type TicketAction,
} from "../../entities/handoff/api";
import { ApiError } from "../../shared/api/client";

/** Tab "Cần xử lý": hội thoại khách cần người, ưu tiên cao nhất rồi cũ nhất trước (bước 2 §12.5). */
export function QueuePage({ onOpen }: { onOpen: (conversationId: string) => void }) {
  const list = useHandoffs();
  const act = useHandoffAction();
  const run = (id: string, action: TicketAction) => act.mutate({ id, action });

  return (
    <div className="admin-page">
      <h1>Cần xử lý</h1>
      {act.isError && (
        <p className="error" role="alert">
          {act.error instanceof ApiError && act.error.status === 409
            ? "Ticket này vừa được người khác xử lý. Đã tải lại danh sách."
            : "Không thực hiện được. Thử lại sau."}
        </p>
      )}
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.isError && (
        <p className="error" role="alert">
          Không tải được danh sách.
        </p>
      )}
      {list.data && list.data.items.length === 0 && <p className="muted">Không có khách nào đang chờ nhân viên.</p>}
      <ul className="plain stack">
        {list.data?.items.map((t) => {
          const prio = priorityBadge(t.priority);
          return (
            <li key={t.id}>
              <article className="card" aria-label={`Ticket của ${t.customer_name ?? "khách"}`}>
                <div className="row">
                  <strong>{t.customer_name ?? "Khách"}</strong>
                  {prio && <span className={prio.className}>{prio.label}</span>}
                  <span className={`badge ${t.status === "open" ? "handoff_suggested" : "ok"}`}>
                    {t.status === "open" ? "Chưa ai nhận" : "Đã có người nhận"}
                  </span>
                  <small className="muted">
                    {sourceLabel(t)} · {waitingLabel(t.opened_at)}
                  </small>
                </div>
                {t.summary ? (
                  <p className="pre">{t.summary}</p>
                ) : (
                  <>
                    {t.last_message_preview && <p className="pre muted">{t.last_message_preview}</p>}
                    {t.summary_status === "pending" && <small className="muted">Đang tóm tắt hội thoại…</small>}
                  </>
                )}
                <div className="row">
                  <button type="button" onClick={() => onOpen(t.conversation_id)}>
                    Mở hội thoại
                  </button>
                  {t.status === "open" && (
                    <button type="button" className="link" disabled={act.isPending} onClick={() => run(t.id, "claim")}>
                      Nhận
                    </button>
                  )}
                  <button type="button" className="link" disabled={act.isPending} onClick={() => run(t.id, "resolve")}>
                    Xong
                  </button>
                  <button type="button" className="link" disabled={act.isPending} onClick={() => run(t.id, "dismiss")}>
                    Không cần (trả lại cho bot)
                  </button>
                </div>
              </article>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
