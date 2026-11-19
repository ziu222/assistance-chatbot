"""Import mọi ORM model để Base.metadata đầy đủ (Alembic env, test drift)."""

import app.modules.conversation.infrastructure.persistence.models  # noqa: F401
import app.modules.customer.infrastructure.persistence.models  # noqa: F401
import app.shared.infrastructure.outbox  # noqa: F401
