import unicodedata
from uuid import UUID

MAX_TEXT_LENGTH = 4000


class InvalidInboundMessage(ValueError):
    pass


def normalize_text(raw: str) -> str:
    """NFC, bỏ ký tự điều khiển (giữ \\n), trim; độ dài tính sau chuẩn hóa."""
    text = unicodedata.normalize("NFC", raw)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) != "Cc")
    text = text.strip()
    if not text:
        raise InvalidInboundMessage("message is empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise InvalidInboundMessage(f"message too long (max {MAX_TEXT_LENGTH})")
    return text


def validate_client_message_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidInboundMessage("client_message_id must be a UUID v4") from exc
    if parsed.version != 4:
        raise InvalidInboundMessage("client_message_id must be a UUID v4")
    return str(parsed)
