import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, api, tokenStore, type WidgetConfig } from "./api";
import { addPending, initialChat, lastSequence, mergeServer, setPending, type ChatState } from "./chat";
import { t } from "./i18n";
import { backoff, nextStep, readableTextOn, safeColor, safeHttpUrl } from "./policy";
import { streamEvents } from "./sse";

const POLL_MS = 30_000; // dự phòng khi SSE chết hẳn (thiết kế §7.4)

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(id);
      resolve();
    });
  });

// Tin nhắn không chứa bí mật nên targetOrigin "*" chấp nhận được; token KHÔNG bao giờ đi qua đây.
const notifyParent = (type: "atlas:close") => window.parent.postMessage({ type }, "*");

export function App({ publicKey }: { publicKey: string }) {
  const [chat, setChat] = useState<ChatState>(initialChat);
  const [config, setConfig] = useState<WidgetConfig>({});
  const [phase, setPhase] = useState<"starting" | "ready" | "error">("starting");
  const [live, setLive] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [draft, setDraft] = useState("");
  const [handoff, setHandoff] = useState<"idle" | "sending" | "done" | "failed">("idle");

  const chatRef = useRef(chat);
  chatRef.current = chat;
  const tokenRef = useRef<string | null>(null);
  const refreshing = useRef<Promise<string> | null>(null);
  const syncing = useRef<{ running: boolean; again: boolean }>({ running: false, again: false });
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLOListElement>(null);
  const stickToBottom = useRef(true);

  // Một lần gọi /sessions dùng chung cho mọi nơi gặp 401 cùng lúc.
  const openSession = useCallback((): Promise<string> => {
    refreshing.current ??= api
      .session(publicKey, tokenRef.current ?? tokenStore.get(publicKey))
      .then((s) => {
        tokenRef.current = s.token;
        tokenStore.set(publicKey, s.token);
        setConfig(s.config);
        return s.token;
      })
      .finally(() => {
        refreshing.current = null;
      });
    return refreshing.current;
  }, [publicKey]);

  // Lấy sự thật qua REST. Gộp các lần gọi chồng nhau: đang chạy thì chỉ đánh dấu chạy lại.
  const sync = useCallback(async (): Promise<void> => {
    const state = syncing.current;
    if (state.running) {
      state.again = true;
      return;
    }
    state.running = true;
    try {
      do {
        state.again = false;
        let after = lastSequence(chatRef.current);
        for (let refreshed = false; ; ) {
          try {
            let page = await api.list(tokenRef.current!, after);
            const known = chatRef.current.conversationId;
            if (page.conversation_id && known && page.conversation_id !== known && after > 0) {
              page = await api.list(tokenRef.current!, 0); // hội thoại mới: tải từ đầu
            }
            setChat((s) => (chatRef.current = mergeServer(s, page.conversation_id, page.messages)));
            if (!page.has_more) break;
            after = lastSequence(chatRef.current);
          } catch (e) {
            if (e instanceof ApiError && e.status === 401 && !refreshed) {
              refreshed = true;
              await openSession();
              continue;
            }
            throw e;
          }
        }
      } while (state.again);
    } finally {
      state.running = false;
    }
  }, [openSession]);

  // Khởi động: phiên (không ghi DB) + lịch sử.
  useEffect(() => {
    openSession()
      .then(() => sync())
      .then(() => setPhase("ready"))
      .catch(() => setPhase("error"));
  }, [openSession, sync]);

  // SSE: tín hiệu -> refetch. Mất kết nối -> backoff + jitter, kết nối lại thì refetch.
  useEffect(() => {
    if (phase !== "ready") return;
    const ctrl = new AbortController();
    void (async () => {
      for (let attempt = 0; !ctrl.signal.aborted; ) {
        try {
          await streamEvents(
            api.eventsUrl,
            tokenRef.current!,
            () => {
              attempt = 0;
              setLive(true);
              void sync().catch(() => {});
            },
            (e) => {
              if (e.event === "signal" || e.event === "resync") void sync().catch(() => {});
            },
            ctrl.signal,
          );
        } catch (e) {
          if (ctrl.signal.aborted) return;
          if ((e as { status?: number }).status === 401) await openSession().catch(() => {});
        }
        setLive(false);
        await sleep(backoff(attempt++, 1000), ctrl.signal);
      }
    })();
    return () => ctrl.abort();
  }, [phase, openSession, sync]);

  // Polling dự phòng khi SSE không sống; refetch khi tab hiện lại.
  useEffect(() => {
    if (phase !== "ready") return;
    const poll = live ? undefined : setInterval(() => void sync().catch(() => {}), POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && void sync().catch(() => {});
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [phase, live, sync]);

  // Loader mở widget -> focus ô nhập. Chỉ nhận lệnh từ trang cha.
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source === window.parent && e.data?.type === "atlas:open") inputRef.current?.focus();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (waitSeconds <= 0) return;
    const id = setTimeout(() => setWaitSeconds((s) => s - 1), 1000);
    return () => clearTimeout(id);
  }, [waitSeconds]);

  // Chỉ tự cuộn khi người dùng đang ở đáy (không giật khi họ đang đọc tin cũ).
  useLayoutEffect(() => {
    const el = logRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [chat]);

  const deliver = useCallback(
    async (cmid: string, text: string) => {
      for (let attempt = 0, refreshed = false; ; ) {
        try {
          await api.send(tokenRef.current!, cmid, text); // cùng cmid cho mọi lần thử
          await sync().catch(() => {});
          return;
        } catch (e) {
          const step = nextStep(e, attempt, refreshed);
          if (step.kind === "retry") {
            attempt++;
            await sleep(step.delayMs);
          } else if (step.kind === "refresh") {
            refreshed = true;
            await openSession().catch(() => {});
          } else {
            if (step.kind === "wait") setWaitSeconds(step.seconds);
            if (e instanceof ApiError && e.status === 401) setFrozen(true);
            setChat((s) => setPending(s, cmid, { status: "failed" }));
            return;
          }
        }
      }
    },
    [openSession, sync],
  );

  const canSend = phase === "ready" && !frozen && waitSeconds <= 0;

  // Bấm nhiều lần vẫn một ticket (server bảo đảm); nút ẩn sau khi đã báo.
  const askStaff = async () => {
    setHandoff("sending");
    for (let refreshed = false; ; ) {
      try {
        await api.handoff(tokenRef.current!);
        setHandoff("done");
        return;
      } catch (e) {
        if (e instanceof ApiError && e.status === 401 && !refreshed) {
          refreshed = true;
          await openSession().catch(() => {});
          continue;
        }
        setHandoff("failed");
        return;
      }
    }
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || !canSend) return;
    const cmid = crypto.randomUUID();
    stickToBottom.current = true;
    setChat((s) => addPending(s, cmid, text));
    setDraft("");
    void deliver(cmid, text);
  };

  const retry = (cmid: string, text: string) => {
    setChat((s) => setPending(s, cmid, { status: "sending" }));
    void deliver(cmid, text);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(); // Shift+Enter: xuống dòng
  };

  const accent = safeColor(config.theme?.color);
  const privacyUrl = safeHttpUrl(config.privacy_url);
  const greeting = typeof config.greeting === "string" && config.greeting ? config.greeting : t.defaultGreeting;
  const botLabel = typeof config.bot_label === "string" && config.bot_label.trim() ? config.bot_label.trim() : t.bot;

  return (
    <div
      className="widget"
      style={accent ? ({ "--accent": accent, "--on-accent": readableTextOn(accent) } as React.CSSProperties) : undefined}
      onKeyDown={(e) => e.key === "Escape" && notifyParent("atlas:close")}
    >
      <header className="bar">
        <h1>{t.title}</h1>
        <button type="button" className="icon" aria-label={t.close} onClick={() => notifyParent("atlas:close")}>
          ×
        </button>
      </header>

      {phase === "error" || frozen ? (
        <p className="banner error" role="alert">
          {t.sessionError}
        </p>
      ) : phase === "ready" && !live ? (
        <p className="banner" role="status">
          {t.reconnecting}
        </p>
      ) : null}

      <ol
        ref={logRef}
        className="log"
        role="log"
        aria-live="polite"
        aria-label={t.log}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        <li className="msg them">{greeting}</li>
        {chat.messages.map((m) => (
          <li
            key={m.id}
            className={
              m.direction === "inbound"
                ? "msg me"
                : m.sender_type === "bot"
                  ? "msg them bot"
                  : "msg them"
            }
          >
            <span className="sr-only">
              {m.direction === "inbound"
                ? t.you
                : m.sender_type === "bot"
                  ? botLabel
                  : m.sender_type === "system"
                    ? t.auto
                    : t.staff}
              :{" "}
            </span>
            {m.sender_type === "bot" && <span className="bot-badge">{botLabel}</span>}
            {m.text /* chỉ text: React escape, không HTML/markdown/linkify (thiết kế §9.3) */}
          </li>
        ))}
        {chat.pending.map((p) => (
          <li key={p.cmid} className={`msg me ${p.status}`}>
            {p.text}
            <span className="meta">
              {p.status === "sending" ? (
                t.sending
              ) : (
                <>
                  {t.failed}{" "}
                  <button type="button" className="link" onClick={() => retry(p.cmid, p.text)} disabled={!canSend}>
                    {t.retry}
                  </button>
                </>
              )}
            </span>
          </li>
        ))}
      </ol>

      {waitSeconds > 0 && (
        <p className="banner" role="status">
          {t.rateLimited(waitSeconds)}
        </p>
      )}

      {chat.conversationId && phase === "ready" && !frozen && (
        <div className="handoff">
          {handoff === "done" ? (
            <p role="status">{t.askStaffDone}</p>
          ) : (
            <>
              <button type="button" className="link" onClick={() => void askStaff()} disabled={handoff === "sending"}>
                {t.askStaff}
              </button>
              {handoff === "failed" && <span role="alert"> {t.askStaffFailed}</span>}
            </>
          )}
        </div>
      )}

      <form className="composer" onSubmit={submit}>
        <label htmlFor="atlas-input" className="sr-only">
          {t.inputLabel}
        </label>
        <textarea
          id="atlas-input"
          ref={inputRef}
          rows={1}
          maxLength={4000}
          placeholder={t.placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" disabled={!canSend || !draft.trim()}>
          {t.send}
        </button>
      </form>

      {privacyUrl && (
        <footer className="foot">
          <a href={privacyUrl} target="_blank" rel="noopener noreferrer">
            {t.privacy}
          </a>
        </footer>
      )}
    </div>
  );
}
