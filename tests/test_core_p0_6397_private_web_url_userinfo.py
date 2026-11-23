"""#6397: authenticated web retention cannot persist URL userinfo."""
import pytest
from cifereca.core.notice_c_authenticated_web_retention import validated_private_web_field


@pytest.mark.parametrize("url", [
    "https://user@example.org/books/123",
    "https://user:secret@example.org/books/123",
    "https://:secret@example.org/books/123",
])
def test_private_reference_rejects_url_userinfo(url):
    raw = {"schema": 1, "connection_epoch": 1, "links": [
        {"url": url, "evidence": "numilog.authenticated_search_product_card"}
    ]}
    assert validated_private_web_field(raw, "numilog") is None
