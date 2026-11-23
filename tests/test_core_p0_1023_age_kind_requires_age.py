"""#1023: age-evidence filter without an age must not silently disappear."""
import pytest
from cifereca.core.notice_c_public_search import search_public_notice_c


class NoStorage:
    def __getattr__(self, name):
        raise AssertionError("invalid age filter reached SQLite")


@pytest.mark.parametrize("kind", ["interest", "reading", "publisher", "recommended"])
def test_public_c_age_kind_requires_age_before_sql(kind):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoStorage(), "roman", age_kind=kind)
