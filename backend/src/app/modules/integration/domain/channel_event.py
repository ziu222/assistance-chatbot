from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class NormalizedChannelEvent:
    """Hợp đồng nội bộ giữa channel adapter và use case (PROJECT_CONTEXT §12.3).

    tenant_id, channel_connection_id, external_customer_id phải lấy từ nguồn đã xác thực
    (token/webhook đã verify), không bao giờ từ request body.
    """

    tenant_id: UUID
    channel_connection_id: UUID
    provider: str
    external_message_id: str
    external_customer_id: str
    direction: Literal["inbound"]
    message_type: Literal["text"]
    text: str
    occurred_at: datetime
    trace_id: str | None
