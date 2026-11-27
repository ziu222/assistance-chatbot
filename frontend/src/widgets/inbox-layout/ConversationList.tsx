import { useConversationList } from "../../entities/conversation/api";
import {
  CHANNEL_FILTERS,
  channelLabel,
  type ChannelProvider,
} from "../../entities/conversation/channel";
import { formatTime } from "../../shared/lib/format";

type Props = {
  selectedId: string | null;
  onSelect: (id: string) => void;
  provider: ChannelProvider | null;
  onProviderChange: (provider: ChannelProvider | null) => void;
};

export function ConversationList({ selectedId, onSelect, provider, onProviderChange }: Props) {
  const { conversations, isPending, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useConversationList(provider);

  return (
    <>
      <div className="channel-filter" role="group" aria-label="Lọc theo kênh">
        {CHANNEL_FILTERS.map((f) => (
          <button
            key={f.id ?? "all"}
            type="button"
            className={provider === f.id ? "active" : ""}
            aria-pressed={provider === f.id}
            onClick={() => onProviderChange(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {isPending ? (
        <p className="state">Đang tải hội thoại…</p>
      ) : isError ? (
        <p className="state error" role="alert">
          Không tải được danh sách.{" "}
          <button type="button" className="link" onClick={() => void refetch()}>
            Thử lại
          </button>
        </p>
      ) : !conversations.length ? (
        <p className="state">
          {provider
            ? `Chưa có hội thoại nào từ ${channelLabel(provider)}.`
            : "Chưa có hội thoại nào. Tin từ widget sẽ hiện ở đây."}
        </p>
      ) : (
        <nav aria-label="Danh sách hội thoại">
          <ul className="conv-list">
            {conversations.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`conv ${c.id === selectedId ? "active" : ""} ${c.unread_count ? "unread" : ""}`}
                  aria-current={c.id === selectedId ? "true" : undefined}
                  onClick={() => onSelect(c.id)}
                >
                  <span className="conv-top">
                    <span className="name">{c.customer.display_name}</span>
                    <span className={`channel-badge channel-${c.connection.provider}`}>
                      {channelLabel(c.connection.provider)}
                    </span>
                    <time dateTime={c.last_message_at ?? undefined}>{formatTime(c.last_message_at)}</time>
                  </span>
                  <span className="conv-bottom">
                    <span className="preview">{c.last_message_preview}</span>
                    {c.unread_count > 0 && (
                      <span className="badge" aria-label={`${c.unread_count} tin chưa đọc`}>
                        {c.unread_count}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {hasNextPage && (
            <button type="button" className="more" onClick={() => void fetchNextPage()} disabled={isFetchingNextPage}>
              {isFetchingNextPage ? "Đang tải…" : "Tải thêm"}
            </button>
          )}
        </nav>
      )}
    </>
  );
}
