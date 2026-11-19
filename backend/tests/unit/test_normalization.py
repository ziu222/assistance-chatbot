import pytest

from app.modules.integration.domain.normalization import (
    InvalidInboundMessage,
    normalize_text,
    validate_client_message_id,
)


def test_nfc_normalizes_decomposed_vietnamese():
    decomposed = "Tiếng Việt"  # dấu tách rời (NFD)
    assert normalize_text(decomposed) == "Tiếng Việt"


def test_strips_control_chars_but_keeps_newline():
    assert normalize_text("a\x00b\x07c\nd\re\tf") == "abc\ndef"


def test_trims_whitespace():
    assert normalize_text("  xin chào \n ") == "xin chào"


@pytest.mark.parametrize("raw", ["", "   ", "\x00\x01", "\n\n"])
def test_rejects_empty_after_normalization(raw):
    with pytest.raises(InvalidInboundMessage, match="empty"):
        normalize_text(raw)


def test_accepts_exactly_4000_chars():
    assert len(normalize_text("a" * 4000)) == 4000


def test_rejects_over_4000_chars():
    with pytest.raises(InvalidInboundMessage, match="too long"):
        normalize_text("a" * 4001)


def test_length_is_counted_after_nfc():
    # 4000 ký tự sau NFC, nhưng 8000 code point ở dạng NFD -> vẫn hợp lệ
    assert len(normalize_text("é" * 4000)) == 4000


def test_client_message_id_accepts_uuid4():
    value = "3f0c8b0e-6a4e-4c39-9d2e-7d1a5b7c9e10"
    assert validate_client_message_id(value) == value


def test_client_message_id_is_canonicalized():
    assert (
        validate_client_message_id("3F0C8B0E-6A4E-4C39-9D2E-7D1A5B7C9E10")
        == "3f0c8b0e-6a4e-4c39-9d2e-7d1a5b7c9e10"
    )


@pytest.mark.parametrize(
    "value",
    ["", "abc", "3f0c8b0e-6a4e-1c39-9d2e-7d1a5b7c9e10", "3f0c8b0e6a4e4c399d2e7d1a5b7c9e10x"],
)
def test_client_message_id_rejects_non_uuid4(value):
    with pytest.raises(InvalidInboundMessage):
        validate_client_message_id(value)
