from app.modules.integration.domain.origin import frame_ancestors, valid_origins


def test_keeps_only_well_formed_origins():
    allowed = [
        "https://shop.vn",
        "HTTPS://Shop2.VN/",  # chuẩn hóa: viết thường, bỏ "/" cuối
        "http://localhost:3000",
        "https://evil.vn; script-src *",  # chèn directive CSP
        "https://a.vn\r\nX-Injected: 1",  # chèn header
        "https://shop.vn/path",
        "*",
        "javascript:alert(1)",
        "",
        42,
    ]
    assert valid_origins(allowed) == [
        "https://shop.vn",
        "https://shop2.vn",
        "http://localhost:3000",
    ]


def test_non_list_config_is_empty():
    assert valid_origins(None) == []
    assert valid_origins("https://shop.vn") == []


def test_frame_ancestors_fails_closed_when_empty():
    assert frame_ancestors([]) == "'none'"
    assert frame_ancestors(["https://a.vn", "https://b.vn"]) == "https://a.vn https://b.vn"
