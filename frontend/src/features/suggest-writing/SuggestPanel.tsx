import { useState } from "react";
import {
  useSuggest,
  useWritingFeedback,
  useWritingRequest,
  type Kind,
  type Level,
} from "../../entities/writing/api";
import { factProblems, writingError } from "../../entities/writing/facts";
import { ApiError } from "../../shared/api/client";

const LEVELS: { id: Level; label: string }[] = [
  { id: "fix", label: "Sửa lỗi chính tả" },
  { id: "polish", label: "Trau chuốt" },
  { id: "brand", label: "Theo giọng shop" },
  { id: "alternatives", label: "3 phương án khác" },
];

/** Gợi ý chỉnh câu chữ. Gợi ý không bao giờ tự lưu: "Dùng" chỉ thay nội dung ô đang sửa. */
export function SuggestPanel({ text, kind, onUse }: { text: string; kind: Kind; onUse: (text: string) => void }) {
  const [level, setLevel] = useState<Level>("polish");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<number[]>([]);
  const suggest = useSuggest();
  const request = useWritingRequest(requestId);

  const start = () => {
    setHidden([]);
    suggest.mutate({ kind, level, text }, { onSuccess: (r) => setRequestId(r.id) });
  };
  const data = request.data;
  const busy = suggest.isPending || (requestId !== null && (!data || data.status === "pending" || data.status === "running"));

  return (
    <div className="suggest">
      <div className="row">
        <select aria-label="Mức gợi ý" value={level} onChange={(e) => setLevel(e.target.value as Level)}>
          {LEVELS.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
        <button type="button" className="link" disabled={busy || !text.trim()} onClick={start}>
          {busy ? "Đang gợi ý…" : "Gợi ý"}
        </button>
      </div>
      {suggest.isError && (
        <p className="error" role="alert">
          {suggest.error instanceof ApiError && suggest.error.status === 429
            ? "Đang có gợi ý chờ xử lý, đợi chút rồi thử lại."
            : "Không gửi được yêu cầu gợi ý."}
        </p>
      )}
      {data?.status === "failed" && (
        <p className="error" role="alert">
          {writingError(data.error_code)}
        </p>
      )}
      {data?.status === "done" &&
        data.suggestions.map((s, i) =>
          hidden.includes(i) ? null : (
            <SuggestionCard
              key={i}
              requestId={data.id}
              index={i}
              text={s.text}
              changes={s.changes}
              problems={factProblems(s.fact_check)}
              blocked={!s.fact_check.ok}
              onUse={() => {
                onUse(s.text);
                setRequestId(null); // đã dùng: đóng danh sách gợi ý
              }}
              onDismiss={() => setHidden((h) => [...h, i])}
            />
          ),
        )}
    </div>
  );
}

function SuggestionCard(props: {
  requestId: string;
  index: number;
  text: string;
  changes: string;
  problems: string[];
  blocked: boolean;
  onUse: () => void;
  onDismiss: () => void;
}) {
  const feedback = useWritingFeedback(props.requestId);
  return (
    <div className={`suggestion ${props.blocked ? "blocked" : ""}`}>
      <p className="pre">{props.text}</p>
      <small className="muted">{props.changes}</small>
      {props.problems.length > 0 && (
        <ul className={props.blocked ? "error" : "muted"}>
          {props.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="row">
        <button
          type="button"
          disabled={props.blocked || feedback.isPending}
          title={props.blocked ? "Gợi ý này đổi thông tin của bạn nên không dùng được" : undefined}
          onClick={() => feedback.mutate({ index: props.index, action: "used" }, { onSuccess: props.onUse })}
        >
          Dùng
        </button>
        <button
          type="button"
          className="link"
          onClick={() => {
            feedback.mutate({ index: props.index, action: "dismissed" });
            props.onDismiss();
          }}
        >
          Bỏ
        </button>
      </div>
    </div>
  );
}
