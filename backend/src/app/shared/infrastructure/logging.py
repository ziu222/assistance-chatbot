"""Log JSON một dòng mỗi bản ghi, kèm trace_id của request hiện tại (thiết kế §13.1).

Quy ước: KHÔNG log nội dung tin, token, IP thô, email. Chỉ log ID, mã lỗi, số đo.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

# Thuộc tính chuẩn của LogRecord; phần còn lại (truyền qua extra=) được đưa vào JSON.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None)
            or getattr(record, "ctx_trace_id", None)
            or trace_id_var.get(),
        }
        entry |= {
            k: v for k, v in vars(record).items() if k not in _STANDARD and k != "ctx_trace_id"
        }
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    # Gắn trace_id lúc TẠO bản ghi (không phải lúc format): handler format muộn vẫn đúng trace.
    base_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        # Tên riêng: nếu đặt "trace_id" thì log nào truyền extra={"trace_id": ...} sẽ KeyError.
        record.ctx_trace_id = trace_id_var.get()
        return record

    logging.setLogRecordFactory(factory)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Access log của uvicorn in IP thô + query string: tắt, app.access đã thay thế (không IP).
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()  # đi qua handler JSON của root
        uv.propagate = True
