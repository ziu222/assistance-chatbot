from dataclasses import dataclass
from uuid import UUID

# ponytail: một luật duy nhất ở bước 1; RBAC đầy đủ thay bảng này, chỗ gọi authorize() giữ nguyên.
_ALLOWED_ROLES = {
    "conversation.read": {"owner", "admin", "agent"},
    "conversation.reply": {"owner", "admin", "agent"},
}


@dataclass(frozen=True)
class StaffActor:
    user_id: UUID
    tenant_id: UUID
    role: str
    email: str
    display_name: str
    tenant_name: str
    tenant_slug: str


def authorize(actor: StaffActor, action: str, resource_tenant_id: UUID) -> bool:
    return actor.tenant_id == resource_tenant_id and actor.role in _ALLOWED_ROLES.get(action, set())
