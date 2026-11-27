"""Quy tắc kiểm tra dữ liệu và hàm thuần cho kết nối kênh bán hàng (O5)."""

import hashlib
import re
import unicodedata

MAX_PAGE_ID_LENGTH = 128
MIN_SECRET_LENGTH = 8
MAX_SECRET_LENGTH = 1024

_PAGE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


class InvalidChannelInput(ValueError):
    """Đầu vào kết nối kênh không hợp lệ."""


def normalize_page_id(raw: str) -> str:
    """Chuẩn hóa và kiểm tra `page_id` (bắt buộc không rỗng, <= 128 ký tự, không khoảng trắng)."""
    if not isinstance(raw, str):
        raise InvalidChannelInput("page_id must be a string")
    value = raw.strip()
    if not value:
        raise InvalidChannelInput("page_id must not be empty")
    if len(value) > MAX_PAGE_ID_LENGTH:
        raise InvalidChannelInput(f"page_id must be at most {MAX_PAGE_ID_LENGTH} characters")
    if not _PAGE_ID_RE.fullmatch(value):
        raise InvalidChannelInput("page_id contains invalid characters")
    return value


def normalize_secret(raw: str, field_name: str) -> str:
    """Chuẩn hóa và kiểm tra token/secret (8..1024 ký tự, không ký tự điều khiển)."""
    if not isinstance(raw, str):
        raise InvalidChannelInput(f"{field_name} must be a string")
    value = raw.strip()
    if not value:
        raise InvalidChannelInput(f"{field_name} must not be empty")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise InvalidChannelInput(f"{field_name} contains invalid control characters")
    if not (MIN_SECRET_LENGTH <= len(value) <= MAX_SECRET_LENGTH):
        raise InvalidChannelInput(
            f"{field_name} must be between {MIN_SECRET_LENGTH} and {MAX_SECRET_LENGTH} characters"
        )
    return value


def verify_token_hash(verify_token: str) -> str:
    """Băm SHA-256 `verify_token` để tra nhanh ở `config->>'verify_token_hash'`."""
    return hashlib.sha256(verify_token.encode("utf-8")).hexdigest()


def build_webhook_url(public_origin: str, provider: str) -> str | None:
    """Trả URL webhook công khai theo provider (`None` nếu không phải `messenger`)."""
    if provider != "messenger":
        return None
    origin = public_origin.strip().rstrip("/")
    return f"{origin}/webhooks/messenger" if origin else "/webhooks/messenger"
