"""#1023: invisible Unicode separators cannot silently alter exact C filters."""
import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c


class NeverAccessStorage:
    def __getattr__(self, name):
        raise AssertionError("invalid filters reached SQLite")


@pytest.mark.parametrize("field", ["provider_ids", "content_types"])
@pytest.mark.parametrize("separator", [chr(0x2028), chr(0x2029)])
def test_public_c_rejects_invisible_filter_separators_before_sql(field, separator):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(
            NeverAccessStorage(), "livre", **{field: ("valid" + separator + "hidden",)},
        )
