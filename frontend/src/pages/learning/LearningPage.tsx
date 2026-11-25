import { useState } from "react";
import {
  useApproveCandidate,
  useCandidates,
  useRejectCandidate,
  type Candidate,
  type CandidateStatus,
} from "../../entities/learning/api";
import { ApiError } from "../../shared/api/client";

const KIND_LABEL: Record<Candidate["kind"], string> = {
  faq_new: "FAQ mới",
  faq_update: "Cập nhật FAQ",
  doc_section: "Đoạn tài liệu",
  brand_example: "Câu mẫu giọng văn",
  order_flow: "Quy trình đặt hàng",
};

const STATUS_LABEL: Record<CandidateStatus, string> = {
  pending: "Chờ duyệt",
  approved: "Đang chờ eval",
  eval_failed: "Eval chưa đạt",
  published: "Đã phát hành",
  rejected: "Đã bỏ",
};

function CandidateCard({
  c,
  canManage,
  onOpenConversation,
}: {
  c: Candidate;
  canManage: boolean;
  onOpenConversation: (id: string) => void;
}) {
  const isDoc = c.kind === "doc_section";
  const titleKey = isDoc ? "title" : "question";
  const bodyKey = isDoc ? "body" : "answer";
  const currentTitle = c.proposal[titleKey] ?? "";
  const currentBody = c.proposal[bodyKey] ?? "";

  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(currentTitle);
  const [body, setBody] = useState(currentBody);
  const [rejectReason, setRejectReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const approve = useApproveCandidate();
  const reject = useRejectCandidate();
  const reviewable = canManage && (c.status === "pending" || c.status === "eval_failed");

  const doApprove = (edited?: Record<string, string>) => {
    setErr(null);
    approve.mutate(
      { id: c.id, body: edited },
      {
        onSuccess: () => setEditing(false),
        onError: (e) => setErr(e instanceof ApiError ? e.message : "Không duyệt được."),
      },
    );
  };

  return (
    <li className="card stack">
      <div className="row">
        <strong>{KIND_LABEL[c.kind]}</strong>
        <span className={`badge ${c.status === "published" ? "approved" : ""}`}>
          {STATUS_LABEL[c.status]}
        </span>
        <span className="muted">
          {c.intent} · {c.topic} · {c.evidence.length} lần sửa
        </span>
      </div>

      {c.current_entry && (
        <div className="muted">
          <small>Nội dung hiện tại:</small>
          <p className="pre">
            {c.current_entry.question ?? c.current_entry.title} — {c.current_entry.answer ?? c.current_entry.body}
          </p>
        </div>
      )}

      {editing ? (
        <div className="stack">
          <label>
            {isDoc ? "Tiêu đề" : "Câu hỏi"}
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            {isDoc ? "Nội dung" : "Trả lời"}
            <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
          <div className="row">
            <button
              type="button"
              disabled={approve.isPending || !title.trim() || !body.trim()}
              onClick={() => doApprove({ [titleKey]: title.trim(), [bodyKey]: body.trim() })}
            >
              Lưu &amp; duyệt
            </button>
            <button type="button" className="link" onClick={() => setEditing(false)}>
              Hủy
            </button>
          </div>
        </div>
      ) : (
        <>
          <strong>{currentTitle}</strong>
          <p className="pre">{currentBody}</p>
        </>
      )}

      {!c.checks.passed && (
        <p className="error" role="alert">
          Kiểm tra tự động chưa đạt: {c.checks.pii?.length ? `PII (${c.checks.pii.join(", ")}) ` : ""}
          {c.checks.customer_overlap ? "trùng chữ khách " : ""}
          {c.checks.ungrounded_facts && Object.keys(c.checks.ungrounded_facts).length > 0
            ? `sự thật chưa khớp (${Object.keys(c.checks.ungrounded_facts).join(", ")})`
            : ""}
        </p>
      )}

      {c.eval_report && !c.eval_report.passed && (
        <div className="error" role="alert">
          <strong>Eval chưa đạt ({c.eval_report.failed_cases.length}/{c.eval_report.total} ca lỗi):</strong>
          <ul className="plain">
            {c.eval_report.failed_cases.map((f) => (
              <li key={f.case_id}>
                <span>{f.question}</span> → <em>{f.reply || "(rỗng)"}</em> ({f.reasons.join(", ")})
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="row">
        <small className="muted">Bằng chứng:</small>
        {Array.from(new Set(c.evidence.map((e) => e.conversation_id))).map((cid, idx) => (
          <button key={cid} type="button" className="link" onClick={() => onOpenConversation(cid)}>
            Hội thoại #{idx + 1}
          </button>
        ))}
      </div>

      {err && (
        <p className="error" role="alert">
          {err}
        </p>
      )}

      {reviewable && !editing && (
        <div className="row">
          <button
            type="button"
            disabled={approve.isPending || !c.checks.passed}
            onClick={() => doApprove()}
          >
            Duyệt
          </button>
          <button type="button" className="link" onClick={() => setEditing(true)}>
            Sửa rồi duyệt
          </button>
          {rejecting ? (
            <>
              <input
                placeholder="Lý do bỏ (tùy chọn)"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
              />
              <button
                type="button"
                disabled={reject.isPending}
                onClick={() =>
                  reject.mutate(
                    { id: c.id, reason: rejectReason.trim() || undefined },
                    { onSuccess: () => setRejecting(false) },
                  )
                }
              >
                Xác nhận bỏ
              </button>
              <button type="button" className="link" onClick={() => setRejecting(false)}>
                Hủy
              </button>
            </>
          ) : (
            <button type="button" className="link" onClick={() => setRejecting(true)}>
              Bỏ
            </button>
          )}
        </div>
      )}
    </li>
  );
}

const FILTERS: { id: CandidateStatus | "all"; label: string }[] = [
  { id: "pending", label: "Chờ duyệt" },
  { id: "eval_failed", label: "Eval chưa đạt" },
  { id: "approved", label: "Đang chờ eval" },
  { id: "published", label: "Đã phát hành" },
  { id: "rejected", label: "Đã bỏ" },
  { id: "all", label: "Tất cả" },
];

export function LearningPage({
  canManage,
  onOpenConversation,
}: {
  canManage: boolean;
  onOpenConversation: (id: string) => void;
}) {
  const [filter, setFilter] = useState<CandidateStatus | "all">("pending");
  const list = useCandidates(filter === "all" ? undefined : filter);
  const items = list.data?.items ?? [];

  return (
    <div className="admin-page">
      <h1>Bot muốn học</h1>
      {!canManage && <p className="muted">Chỉ chủ shop/admin được duyệt ứng viên tri thức.</p>}
      <div className="tabs" role="tablist">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            className={filter === f.id ? "active" : ""}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.isError && (
        <p className="error" role="alert">
          Không tải được danh sách ứng viên.
        </p>
      )}
      {list.data && items.length === 0 && <p className="muted">Chưa có ứng viên nào ở mục này.</p>}
      <ul className="plain stack">
        {items.map((c) => (
          <CandidateCard
            key={c.id}
            c={c}
            canManage={canManage}
            onOpenConversation={onOpenConversation}
          />
        ))}
      </ul>
    </div>
  );
}
