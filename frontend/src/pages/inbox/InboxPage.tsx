import type { ChannelProvider } from "../../entities/conversation/channel";
import { ConversationWorkspace } from "../../widgets/conversation-workspace/ConversationWorkspace";
import { ConversationList } from "../../widgets/inbox-layout/ConversationList";

export function InboxPage({
  selectedId,
  onSelect,
  provider,
  onProviderChange,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  provider: ChannelProvider | null;
  onProviderChange: (provider: ChannelProvider | null) => void;
}) {
  return (
    <>
      <aside className="sidebar">
        <ConversationList
          selectedId={selectedId}
          onSelect={onSelect}
          provider={provider}
          onProviderChange={onProviderChange}
        />
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
