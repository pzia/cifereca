"""#1023: Unicode paragraph/line separators are not documentary query text."""
import pytest
from cifereca.core.notice_c_public_search import search_public_notice_c


class NoStorage:
    def __getattr__(self, name):
        raise AssertionError("invalid query accessed SQLite")


@pytest.mark.parametrize("character", ["\\u2028", "\\u2029"])
def test_public_c_query_rejects_invisible_separator_before_sql(character):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoStorage(), "roman" + character + "invisible")
