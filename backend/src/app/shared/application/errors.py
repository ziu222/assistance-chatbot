class RetryableConflict(RuntimeError):
    """Xung đột tạm thời của DB (deadlock/serialization); thao tác có thể chạy lại an toàn."""


class TransientDatabaseError(RuntimeError):
    """Xung đột tạm thời vẫn còn sau khi đã retry; client nên thử lại (HTTP 503)."""
