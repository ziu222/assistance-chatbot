import { useState, type FormEvent } from "react";
import {
  useAgents,
  useAiSettings,
  useConnections,
  useCreateAgent,
  useTemplates,
  useUpdateAgent,
  useUpdateAiSettings,
  type Agent,
} from "../../entities/agent/api";
import { ApiError } from "../../shared/api/client";

const MICRO = 1_000_000;

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "version_conflict") return "Agent vừa được người khác sửa. Đã tải lại, hãy thử lại.";
    if (e.code === "agent_exists") return "Kênh này đã có agent.";
    if (e.status === 403) return "Bạn không có quyền thực hiện thao tác này.";
  }
  return "Không thực hiện được. Thử lại sau.";
}

function AiSwitch({ canManage }: { canManage: boolean }) {
  const settings = useAiSettings();
  const update = useUpdateAiSettings();
  const [cap, setCap] = useState<string | null>(null);
  if (!settings.data) return <p className="state">Đang tải…</p>;
  const { ai_enabled, daily_cost_cap_micro } = settings.data;
  const capUsd = cap ?? String(daily_cost_cap_micro / MICRO);

  return (
    <section className="card" aria-labelledby="ai-switch">
      <h2 id="ai-switch">AI của cửa hàng</h2>
      <label className="row">
        <input
          type="checkbox"
          checked={ai_enabled}
          disabled={!canManage || update.isPending}
          onChange={(e) => update.mutate({ ai_enabled: e.target.checked })}
        />
        <span>
          {ai_enabled ? "AI đang bật" : "AI đang tắt"} <small className="muted">(kill switch: tắt là dừng mọi run ngay)</small>
        </span>
      </label>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          const usd = Number(capUsd);
          if (Number.isFinite(usd) && usd >= 0) update.mutate({ daily_cost_cap_micro: Math.round(usd * MICRO) });
          setCap(null);
        }}
      >
        <label>
          Trần chi phí mỗi ngày (USD){" "}
          <input
            type="number"
            min="0"
            step="0.5"
            value={capUsd}
            disabled={!canManage}
            onChange={(e) => setCap(e.target.value)}
          />
        </label>
        <button type="submit" disabled={!canManage || cap === null || update.isPending}>
          Lưu
        </button>
      </form>
      {update.isError && (
        <p className="error" role="alert">
          {errorText(update.error)}
        </p>
      )}
    </section>
  );
}

function AgentCard({ agent, canManage }: { agent: Agent; canManage: boolean }) {
  const update = useUpdateAgent();
  const [rate, setRate] = useState(String(agent.shadow_sample_rate));
  const busy = update.isPending;
  const rateNum = Number(rate);
  const rateValid = Number.isFinite(rateNum) && rateNum >= 0 && rateNum <= 1;

  return (
    <article className="card" aria-label={agent.name}>
      <h3>{agent.name}</h3>
      <p className="muted">
        Template {agent.template_key} v{agent.template_version} · phiên bản cấu hình #{agent.current_version.version_number}
      </p>
      <div className="row">
        <label>
          Chế độ{" "}
          <select
            value={agent.mode}
            disabled={!canManage || busy}
            onChange={(e) => update.mutate({ id: agent.id, version: agent.version, mode: e.target.value as Agent["mode"] })}
          >
            <option value="manual">Manual (không chạy AI)</option>
            <option value="shadow">Shadow (soạn nháp, không gửi)</option>
          </select>
        </label>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            if (rateValid) update.mutate({ id: agent.id, version: agent.version, shadow_sample_rate: rateNum });
          }}
        >
          <label>
            Tỉ lệ lấy mẫu (0–1){" "}
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={rate}
              disabled={!canManage || busy}
              onChange={(e) => setRate(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={!canManage || busy || !rateValid || rateNum === agent.shadow_sample_rate}
          >
            Lưu
          </button>
        </form>
      </div>
      {agent.mode === "shadow" && (
        <p className="muted">Nháp chỉ hiện ở màn hình Trace, không bao giờ gửi tới khách.</p>
      )}
      {update.isError && (
        <p className="error" role="alert">
          {errorText(update.error)}
        </p>
      )}
    </article>
  );
}

function CreateAgent() {
  const templates = useTemplates();
  const connections = useConnections();
  const create = useCreateAgent();
  const [template, setTemplate] = useState("");
  const [connection, setConnection] = useState("");
  const [name, setName] = useState("");

  const free = (connections.data?.items ?? []).filter((c) => c.agent_id === null && c.status === "active");
  const tpls = templates.data?.items ?? [];
  const chosenTemplate = template || tpls[0]?.key || "";
  const chosenConnection = connection || free[0]?.id || "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { template_key: chosenTemplate, channel_connection_id: chosenConnection, name: name.trim() },
      { onSuccess: () => setName("") },
    );
  };

  return (
    <section className="card" aria-labelledby="new-agent">
      <h2 id="new-agent">Tạo agent từ template</h2>
      {free.length === 0 ? (
        <p className="muted">Mọi kênh đang hoạt động đều đã có agent.</p>
      ) : (
        <form className="stack" onSubmit={submit}>
          <label>
            Template{" "}
            <select value={chosenTemplate} onChange={(e) => setTemplate(e.target.value)}>
              {tpls.map((t) => (
                <option key={`${t.key}.${t.version}`} value={t.key}>
                  {t.key} (v{t.version})
                </option>
              ))}
            </select>
          </label>
          <label>
            Kênh{" "}
            <select value={chosenConnection} onChange={(e) => setConnection(e.target.value)}>
              {free.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.provider} · {c.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tên agent{" "}
            <input value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
          </label>
          <p className="muted">Agent mới luôn bắt đầu ở chế độ Manual; bạn chủ động bật Shadow sau.</p>
          <button type="submit" disabled={create.isPending || !name.trim() || !chosenTemplate}>
            Tạo agent
          </button>
          {create.isError && (
            <p className="error" role="alert">
              {errorText(create.error)}
            </p>
          )}
        </form>
      )}
    </section>
  );
}

export function AgentPage({ canManage }: { canManage: boolean }) {
  const agents = useAgents();
  return (
    <div className="admin-page">
      <h1>Agent</h1>
      {!canManage && <p className="muted">Bạn chỉ có quyền xem. Chủ cửa hàng hoặc quản trị viên mới đổi được cấu hình.</p>}
      <AiSwitch canManage={canManage} />
      {agents.isPending && <p className="state">Đang tải…</p>}
      {agents.isError && (
        <p className="error" role="alert">
          Không tải được danh sách agent.
        </p>
      )}
      {agents.data?.items.map((a) => <AgentCard key={`${a.id}.${a.version}`} agent={a} canManage={canManage} />)}
      {canManage && <CreateAgent />}
    </div>
  );
}
