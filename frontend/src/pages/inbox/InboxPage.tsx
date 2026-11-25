import { ConversationWorkspace } from "../../widgets/conversation-workspace/ConversationWorkspace";
import { ConversationList } from "../../widgets/inbox-layout/ConversationList";

export function InboxPage({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      <aside className="sidebar">
        <ConversationList selectedId={selectedId} onSelect={onSelect} />
      </aside>
      {selectedId ? (
        <ConversationWorkspace key={selectedId} conversationId={selectedId} />
      ) : (
        <section className="workspace">
          <p className="state">Chọn một hội thoại để xem.</p>
        </section>
      )}
    </>
  );
}
