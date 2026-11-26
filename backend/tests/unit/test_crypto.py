"""Kiểm thử mã hóa/giải mã Fernet cho credentials kênh ngoài."""

import pytest
from cryptography.fernet import Fernet

from app.shared.infrastructure.crypto import (
    CredentialsKeyNotConfigured,
    InvalidEncryptedPayload,
    decrypt_json,
    encrypt_json,
)


def test_encrypt_decrypt_roundtrip():
    key = Fernet.generate_key().decode("ascii")
    payload = {
        "app_secret": "sec_123",
        "verify_token": "tok_abc",
        "page_access_token": "EAAB_xyz",
    }
    token = encrypt_json(payload, key)
    assert isinstance(token, str)
    assert "sec_123" not in token
    assert "EAAB_xyz" not in token
    assert decrypt_json(token, key) == payload


def test_wrong_key_raises_invalid_encrypted_payload():
    key_a = Fernet.generate_key().decode("ascii")
    key_b = Fernet.generate_key().decode("ascii")
    token = encrypt_json({"page_access_token": "secret"}, key_a)

    with pytest.raises(InvalidEncryptedPayload):
        decrypt_json(token, key_b)

    with pytest.raises(InvalidEncryptedPayload):
        decrypt_json("tampered-token", key_a)


def test_empty_key_raises_credentials_key_not_configured():
    for empty_key in ("", "   "):
        with pytest.raises(CredentialsKeyNotConfigured):
            encrypt_json({"a": 1}, empty_key)
        with pytest.raises(CredentialsKeyNotConfigured):
            decrypt_json("anything", empty_key)
