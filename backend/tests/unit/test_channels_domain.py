"""Kiểm thử domain thuần cho quản lý kết nối kênh và quyền `channels.manage`."""

import hashlib
from uuid import uuid4

import pytest

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.integration.domain.channels import (
    MAX_PAGE_ID_LENGTH,
    MAX_SECRET_LENGTH,
    MIN_SECRET_LENGTH,
    InvalidChannelInput,
    build_webhook_url,
    normalize_page_id,
    normalize_secret,
    verify_token_hash,
)
from app.modules.integration.infrastructure.messenger.connections import (
    verify_token_hash as infra_verify_token_hash,
)


def test_normalize_page_id_valid_and_invalid_cases():
    assert normalize_page_id("  100200300400  ") == "100200300400"
    assert normalize_page_id("page_1.test-ok") == "page_1.test-ok"
    assert normalize_page_id("a" * MAX_PAGE_ID_LENGTH) == "a" * MAX_PAGE_ID_LENGTH

    for bad in ("", "   ", "100 200", "100\t200", "100\n200", "page/1", "page?x=1", "a" * 129):
        with pytest.raises(InvalidChannelInput):
            normalize_page_id(bad)

    with pytest.raises(InvalidChannelInput):
        normalize_page_id(123)  # type: ignore[arg-type]


def test_normalize_secret_valid_bounds_and_never_leaks_value():
    assert normalize_secret("  " + "s" * MIN_SECRET_LENGTH + "  ", "app_secret") == (
        "s" * MIN_SECRET_LENGTH
    )
    assert normalize_secret("k" * MAX_SECRET_LENGTH, "page_access_token") == "k" * MAX_SECRET_LENGTH

    secret_val = "short!"
    with pytest.raises(InvalidChannelInput) as exc_info:
        normalize_secret(secret_val, "app_secret")
    assert "app_secret" in str(exc_info.value)
    assert secret_val not in str(exc_info.value)

    for bad in ("", "        ", "x" * (MAX_SECRET_LENGTH + 1), "abc\ndef123", "abc\x00def123"):
        with pytest.raises(InvalidChannelInput):
            normalize_secret(bad, "verify_token")


def test_verify_token_hash_matches_sha256_and_messenger_lookup():
    token = "meta-verify-token-123"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert verify_token_hash(token) == expected
    assert infra_verify_token_hash(token) == expected


def test_build_webhook_url():
    assert (
        build_webhook_url("https://api.shop.vn/", "messenger")
        == "https://api.shop.vn/webhooks/messenger"
    )
    assert (
        build_webhook_url("  https://api.shop.vn// ", "messenger")
        == "https://api.shop.vn/webhooks/messenger"
    )
    assert build_webhook_url("https://api.shop.vn", "webchat") is None
    assert build_webhook_url("https://api.shop.vn", "zalo_oa") is None


def test_channels_manage_rbac():
    tenant_id = uuid4()
    other_tenant = uuid4()

    def make_actor(role: str, tid=tenant_id) -> StaffActor:
        return StaffActor(
            user_id=uuid4(),
            tenant_id=tid,
            role=role,
            email=f"{role}@shop.vn",
            display_name=role,
            tenant_name="Shop",
            tenant_slug="shop",
        )

    assert authorize(make_actor("owner"), "channels.manage", tenant_id) is True
    assert authorize(make_actor("admin"), "channels.manage", tenant_id) is True
    assert authorize(make_actor("agent"), "channels.manage", tenant_id) is False
    assert authorize(make_actor("owner", other_tenant), "channels.manage", tenant_id) is False
