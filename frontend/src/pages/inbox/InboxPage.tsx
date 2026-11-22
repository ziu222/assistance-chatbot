import { useEffect, useState } from "react";
import type { Me } from "../../features/authenticate-user/api";
import { useLogout } from "../../features/authenticate-user/api";
import { ConversationWorkspace } from "../../widgets/conversation-workspace/ConversationWorkspace";
import { ConversationList } from "../../widgets/inbox-layout/ConversationList";
import { useEventSignals } from "../../shared/lib/useEventSignals";
import { AgentPage } from "../agent/AgentPage";
import { KnowledgePage } from "../knowledge/KnowledgePage";
import { PolicyPage } from "../policy/PolicyPage";
import { QueuePage } from "../queue/QueuePage";
import { useHandoffs } from "../../entities/handoff/api";
import { TracePage } from "../trace/TracePage";

// Màn hình, hội thoại và run đang chọn nằm trên URL để reload/chia sẻ link giữ nguyên chỗ.
type View = "inbox" | "queue" | "agent" | "policy" | "knowledge" | "trace";
const VIEWS: { id: View; label: string }[] = [
  { id: "inbox", label: "Hộp thư" },
  { id: "queue", label: "Cần xử lý" },
  { id: "agent", label: "Agent" },
  { id: "policy", label: "Tin tự động" },
  { id: "knowledge", label: "Kiến thức" },
  { id: "trace", label: "Trace" },
];
const param = (name: string) => new URLSearchParams(location.search).get(name);
const readView = (): View => VIEWS.find((v) => v.id === param("view"))?.id ?? "inbox";

export function InboxPage({ me, onUnauthorized }: { me: Me; onUnauthorized: () => void }) {
  const [view, setView] = useState<View>(readView);
  const [selected, setSelected] = useState<string | null>(() => param("c"));
  const [run, setRun] = useState<string | null>(() => param("run"));
  const live = useEventSignals(onUnauthorized); // onUnauthorized đã ổn định (useCallback ở App)
  const logout = useLogout();
  const waiting = useHandoffs().data?.items.filter((t) => t.status === "open").length ?? 0;
  const canManage = me.role === "owner" || me.role === "admin";

  useEffect(() => {
    const onPop = () => {
      setView(readView());
      setSelected(param("c"));
      setRun(param("run"));
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = (next: View) => {
    history.pushState(null, "", next === "inbox" ? "?" : `?view=${next}`);
    setView(next);
    setSelected(null);
    setRun(null);
  };
  const select = (id: string) => {
    history.pushState(null, "", `?c=${encodeURIComponent(id)}`);
    setSelected(id);
  };
  const openConversation = (id: string) => {
    setView("inbox");
    select(id);
  };
  const selectRun = (id: string | null) => {
    history.pushState(null, "", id ? `?view=trace&run=${encodeURIComponent(id)}` : "?view=trace");
    setRun(id);
  };

  return (
    <div className={view === "inbox" ? "inbox" : "inbox admin"}>
      <header className="topbar">
        <strong>{me.tenant.name}</strong>
        <nav aria-label="Màn hình" className="nav">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              className={v.id === view ? "active" : ""}
              aria-current={v.id === view ? "page" : undefined}
              onClick={() => go(v.id)}
            >
              {v.label}
              {v.id === "queue" && waiting > 0 && <span className="count"> {waiting}</span>}
            </button>
          ))}
        </nav>
        <span className={`live ${live ? "on" : ""}`} role="status">
          {live ? "Trực tuyến" : "Đang kết nối lại…"}
        </span>
        <span className="who">{me.user.display_name}</span>
        <button type="button" className="link" onClick={() => logout.mutate()}>
          Đăng xuất
        </button>
      </header>
      {view === "inbox" && (
        <>
          <aside className="sidebar">
            <ConversationList selectedId={selected} onSelect={select} />
          </aside>
          {selected ? (
            <ConversationWorkspace key={selected} conversationId={selected} />
          ) : (
            <section className="workspace">
              <p className="state">Chọn một hội thoại để xem.</p>
            </section>
          )}
        </>
      )}
      {view === "queue" && (
        <main className="admin-main">
          <QueuePage onOpen={openConversation} />
        </main>
      )}
      {view === "agent" && (
        <main className="admin-main">
          <AgentPage canManage={canManage} />
        </main>
      )}
      {view === "policy" && (
        <main className="admin-main">
          <PolicyPage canManage={canManage} />
        </main>
      )}
      {view === "knowledge" && (
        <main className="admin-main">
          <KnowledgePage canManage={canManage} />
        </main>
      )}
      {view === "trace" && (
        <main className="admin-main wide">
          <TracePage selected={run} onSelect={selectRun} />
        </main>
      )}
    </div>
  );
}
