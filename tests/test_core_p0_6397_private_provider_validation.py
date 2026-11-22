"""#6397: private web evidence must never invoke foreign provider equality/addition."""
import pytest

from cifereca.core.notice_c_authenticated_web_retention import validated_private_web_field


class HostileProvider:
    def __add__(self, other):
        raise AssertionError("foreign provider addition executed")


@pytest.mark.parametrize("provider", [
    HostileProvider(), None, 123, True, "", "provider\\u200b", "provider\\n",
    "provider/foreign", "provider:" + "a" * 80,
])
def test_private_web_evidence_rejects_non_native_provider_before_dispatch(provider):
    assert validated_private_web_field({
        "schema": 1, "connection_epoch": 1, "links": [{
            "url": "https://example.org/catalogue/book/42",
            "evidence": "provider.authenticated_search_product_card",
        }],
    }, provider) is None
