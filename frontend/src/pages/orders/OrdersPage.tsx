import { useState } from "react";
import { FIELD_LABELS, useOrderAction, useOrderRequest, useOrderRequests, type OrderRequest } from "../../entities/order/api";
import { ApiError } from "../../shared/api/client";

const VND = new Intl.NumberFormat("vi-VN");

/** Chi tiết một phiếu: số điện thoại đầy đủ và tồn kho hiện tại (có thể đã hết sau khi ghi phiếu). */
function Detail({ id }: { id: string }) {
  const q = useOrderRequest(id);
  if (!q.data) return <p className="state">Đang tải…</p>;
  const c = q.data.customer;
  return (
    <dl className="stack">
      {Object.entries(c).map(([k, v]) => (
        <div key={k}>
          <dt>
            {FIELD_LABELS[k] ?? k}
            {q.data.unverified.includes(k) && <small className="muted"> (bot ghi, chưa xác minh)</small>}
          </dt>
          <dd>{v}</dd>
        </div>
      ))}
      {q.data.items.map((i) =>
        i.stock_now === 0 ? (
          <p key={i.sku} className="error" role="alert">
            {i.name} {i.variant} hiện đã hết hàng.
          </p>
        ) : null,
      )}
    </dl>
  );
}

function Card({ o, open, onToggle }: { o: OrderRequest; open: boolean; onToggle: () => void }) {
  const act = useOrderAction();
  return (
    <article className="card" aria-label={`Phiếu đặt hàng của ${o.customer_name ?? "khách"}`}>
      <div className="row">
        <strong>{o.customer_name ?? "Khách"}</strong>
        <span className={`badge ${o.status === "ready" ? "handoff_suggested" : "ok"}`}>
          {o.status !== "ready"
            ? "Bot đang hỏi thêm"
            : o.destination === "staff_confirms"
              ? "Đủ thông tin, chờ xác nhận"
              : "Đã gửi link, khách tự đặt"}
        </span>
        <small className="muted">{o.channel}</small>
      </div>
      <ul className="plain">
        {o.items.map((i) => (
          <li key={i.sku}>
            {i.name} {i.variant ?? ""} × {i.quantity || "?"} · {VND.format(i.unit_price_vnd)} đ
          </li>
        ))}
      </ul>
      <p>
        Tạm tính {VND.format(o.subtotal_vnd)} đ <small className="muted">(chưa gồm phí ship)</small>
        {o.customer.phone && <> · SĐT {o.customer.phone}</>}
      </p>
      {o.missing.length > 0 && (
        <p className="muted">Còn thiếu: {o.missing.map((f) => FIELD_LABELS[f] ?? f).join(", ")}</p>
      )}
      {open && <Detail id={o.id} />}
      {act.isError && (
        <p className="error" role="alert">
          {act.error instanceof ApiError && act.error.status === 409
            ? "Phiếu này vừa được người khác xử lý."
            : "Không thực hiện được. Thử lại sau."}
        </p>
      )}
      <div className="row">
        <button type="button" className="link" onClick={onToggle}>
          {open ? "Ẩn chi tiết" : "Xem chi tiết"}
        </button>
        <button type="button" disabled={act.isPending} onClick={() => act.mutate({ id: o.id, action: "confirm" })}>
          Xác nhận
        </button>
        <button type="button" className="link" disabled={act.isPending} onClick={() => act.mutate({ id: o.id, action: "cancel" })}>
          Hủy
        </button>
      </div>
    </article>
  );
}

/** "Đơn chờ xác nhận" (thiết kế 03 §5.3). Màn hình thử nghiệm: đủ để kiểm tra backend. */
export function OrdersPage() {
  const list = useOrderRequests();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="admin-page">
      <h1>Đơn chờ xác nhận</h1>
      {list.isPending && <p className="state">Đang tải…</p>}
      {list.isError && (
        <p className="error" role="alert">
          Không tải được danh sách.
        </p>
      )}
      {list.data && list.data.items.length === 0 && <p className="muted">Chưa có phiếu đặt hàng nào.</p>}
      <ul className="plain stack">
        {list.data?.items.map((o) => (
          <li key={o.id}>
            <Card o={o} open={open === o.id} onToggle={() => setOpen(open === o.id ? null : o.id)} />
          </li>
        ))}
      </ul>
    </div>
  );
}
