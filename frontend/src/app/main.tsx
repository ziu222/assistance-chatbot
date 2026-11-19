import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { StrictMode, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { LoginForm } from "../features/authenticate-user/LoginForm";
import { meKey, useMe } from "../features/authenticate-user/api";
import { InboxPage } from "../pages/inbox/InboxPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function App() {
  const qc = useQueryClient();
  const me = useMe();
  const onUnauthorized = useCallback(() => qc.setQueryData(meKey, null), [qc]);

  if (me.isPending) return <p className="state center">Đang tải…</p>;
  if (me.isError)
    return (
      <p className="state center error" role="alert">
        Không kết nối được máy chủ.
      </p>
    );
  return me.data ? <InboxPage me={me.data} onUnauthorized={onUnauthorized} /> : <LoginForm />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
