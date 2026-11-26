"""Mã hóa đối xứng credentials kênh ngoài bằng Fernet (ADR-0036).

Không bao giờ lưu plaintext hay log khóa/token. Nếu chưa cấu hình khóa ⇒ lỗi rõ ràng.
"""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialsKeyNotConfigured(RuntimeError):
    pass


class InvalidEncryptedPayload(ValueError):
    pass


def encrypt_json(data: dict[str, Any], key: str) -> str:
    """Mã hóa dict thành chuỗi Fernet; nâng `CredentialsKeyNotConfigured` nếu khóa rỗng/hỏng."""
    if not key or not key.strip():
        raise CredentialsKeyNotConfigured("CHANNEL_CREDENTIALS_KEY is not configured")
    try:
        fernet = Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise CredentialsKeyNotConfigured("CHANNEL_CREDENTIALS_KEY is invalid") from exc
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return fernet.encrypt(payload).decode("ascii")


def decrypt_json(token: str, key: str) -> dict[str, Any]:
    """Giải mã chuỗi Fernet thành dict; sai khóa hoặc payload hỏng ⇒ `InvalidEncryptedPayload`."""
    if not key or not key.strip():
        raise CredentialsKeyNotConfigured("CHANNEL_CREDENTIALS_KEY is not configured")
    try:
        fernet = Fernet(key.encode("ascii"))
        raw = fernet.decrypt(token.encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, UnicodeError) as exc:
        raise InvalidEncryptedPayload("Invalid encrypted channel credentials") from exc
    if not isinstance(parsed, dict):
        raise InvalidEncryptedPayload("Encrypted channel credentials must be a JSON object")
    return parsed
