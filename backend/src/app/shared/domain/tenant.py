from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TenantContext:
    """Tenant đã xác thực (từ session/token). Mọi repository bắt buộc nhận cái này."""

    tenant_id: UUID
