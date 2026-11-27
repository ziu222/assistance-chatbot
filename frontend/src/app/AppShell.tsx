import { useEffect, useState } from "react";
import { parseChannelProvider, type ChannelProvider } from "../entities/conversation/channel";
import { useHandoffs } from "../entities/handoff/api";
import type { Me } from "../features/authenticate-user/api";
import { useLogout } from "../features/authenticate-user/api";
import { AgentPage } from "../pages/agent/AgentPage";
import { InboxPage } from "../pages/inbox/InboxPage";
import { KnowledgePage } from "../pages/knowledge/KnowledgePage";
import { LearningPage } from "../pages/learning/LearningPage";
import { OrdersPage } from "../pages/orders/OrdersPage";
import { PolicyPage } from "../pages/policy/PolicyPage";
import { QueuePage } from "../pages/queue/QueuePage";
import { ShadowPage } from "../pages/shadow/ShadowPage";
import { TracePage } from "../pages/trace/TracePage";
import { useEventSignals } from "../shared/lib/useEventSignals";

// Màn hình, hội thoại và run đang chọn nằm trên URL để reload/chia sẻ link giữ nguyên chỗ.
type View =
  | "inbox"
  | "queue"
  | "orders"
  | "agent"
  | "policy"
  | "knowledge"
  | "learning"
  | "shadow"
  | "trace";
const VIEWS: { id: View; label: string }[] = [
  { id: "inbox", label: "Hộp thư" },
  { id: "queue", label: "Cần xử lý" },
  { id: "orders", label: "Đơn hàng" },
  { id: "agent", label: "Agent" },
  { id: "policy", label: "Tin tự động" },
  { id: "knowledge", label: "Kiến thức" },
  { id: "learning", label: "Bot muốn học" },
  { id: "shadow", label: "Chấm nháp" },
  { id: "trace", label: "Trace" },
];
const param = (name: string) => new URLSearchParams(location.search).get(name);
const readView = (): View => VIEWS.find((v) => v.id === param("view"))?.id ?? "inbox";
const readProvider = (): ChannelProvider | null => parseChannelProvider(param("provider"));
const inboxUrl = (provider: ChannelProvider | null, c: string | null) => {
  const sp = new URLSearchParams();
  if (provider) sp.set("provider", provider);
  if (c) sp.set("c", c);
  const q = sp.toString();
  return q ? `?${q}` : "?";
};

export function AppShell({ me, onUnauthorized }: { me: Me; onUnauthorized: () => void }) {
  const [view, setView] = useState<View>(readView);
  const [selected, setSelected] = useState<string | null>(() => param("c"));
  const [provider, setProvider] = useState<ChannelProvider | null>(readProvider);
  const [run, setRun] = useState<string | null>(() => param("run"));
  const live = useEventSignals(onUnauthorized); // onUnauthorized đã ổn định (useCallback ở App)
  const logout = useLogout();
  const waiting = useHandoffs().data?.items.filter((t) => t.status === "open").length ?? 0;
  const canManage = me.role === "owner" || me.role === "admin";

  useEffect(() => {
    const onPop = () => {
      setView(readView());
      setSelected(param("c"));
      setProvider(readProvider());
      setRun(param("run"));
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = (next: View) => {
    history.pushState(null, "", next === "inbox" ? "?" : `?view=${next}`);
    setView(next);
    setSelected(null);
    setProvider(null);
    setRun(null);
  };
  const select = (id: string) => {
    history.pushState(null, "", inboxUrl(provider, id));
    setSelected(id);
  };
  const selectProvider = (next: ChannelProvider | null) => {
    history.pushState(null, "", inboxUrl(next, selected));
    setProvider(next);
  };
  const openConversation = (id: string) => {
    history.pushState(null, "", inboxUrl(null, id));
    setView("inbox");
    setProvider(null);
    setSelected(id);
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
        <InboxPage
          selectedId={selected}
          onSelect={select}
          provider={provider}
          onProviderChange={selectProvider}
        />
      )}
      {view === "queue" && (
        <main className="admin-main">
          <QueuePage onOpen={openConversation} />
        </main>
      )}
      {view === "orders" && (
        <main className="admin-main">
          <OrdersPage />
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
      {view === "learning" && (
        <main className="admin-main">
          <LearningPage canManage={canManage} onOpenConversation={openConversation} />
        </main>
      )}
      {view === "shadow" && (
        <main className="admin-main">
          {canManage ? (
            <ShadowPage isOwner={me.role === "owner"} />
          ) : (
            <p className="muted">Chỉ chủ shop/admin gán nhãn.</p>
          )}
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
