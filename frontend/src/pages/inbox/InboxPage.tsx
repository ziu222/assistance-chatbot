import { useEffect, useState } from "react";
import type { Me } from "../../features/authenticate-user/api";
import { useLogout } from "../../features/authenticate-user/api";
import { ConversationWorkspace } from "../../widgets/conversation-workspace/ConversationWorkspace";
import { ConversationList } from "../../widgets/inbox-layout/ConversationList";
import { useEventSignals } from "../../shared/lib/useEventSignals";

// Hội thoại đang chọn nằm trên URL (?c=) để reload/chia sẻ link giữ nguyên chỗ.
const readSelected = () => new URLSearchParams(location.search).get("c");

export function InboxPage({ me, onUnauthorized }: { me: Me; onUnauthorized: () => void }) {
  const [selected, setSelected] = useState<string | null>(readSelected);
  const live = useEventSignals(onUnauthorized); // onUnauthorized đã ổn định (useCallback ở App)
  const logout = useLogout();

  useEffect(() => {
    const onPop = () => setSelected(readSelected());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const select = (id: string) => {
    history.pushState(null, "", `?c=${encodeURIComponent(id)}`);
    setSelected(id);
  };

  return (
    <div className="inbox">
      <header className="topbar">
        <strong>{me.tenant.name}</strong>
        <span className={`live ${live ? "on" : ""}`} role="status">
          {live ? "Trực tuyến" : "Đang kết nối lại…"}
        </span>
        <span className="who">{me.user.display_name}</span>
        <button type="button" className="link" onClick={() => logout.mutate()}>
          Đăng xuất
        </button>
      </header>
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
    </div>
  );
}
