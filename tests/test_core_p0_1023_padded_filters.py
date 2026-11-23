"""#1023: padded C selectors must not silently miss exact catalogue sources."""
import pytest
from cifereca.core.notice_c_public_search import search_public_notice_c

class NoStorage:
    def __getattr__(self, name):
        raise AssertionError("invalid filter reached storage")

@pytest.mark.parametrize("field,selector", [
    ("provider_ids", " numilog"), ("provider_ids", "numilog "),
    ("content_types", " ebook"), ("content_types", "ebook "),
])
def test_reject_padded_exact_selector_before_storage(field, selector):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoStorage(), "roman", **{field: (selector,)})
