"""#1023: public C search refuses invisible control text before SQLite access."""
from contextlib import closing

import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage


@pytest.mark.parametrize("query", [
    "Cu\u200bisine", "Cu\u202eisine", "Cu\u2066isine",
    "Cu\u2069isine", "Cu\x00isine", "Cu\nisine", "Cu\ud800isine",
])
def test_invisible_controls_refuse_before_database_access(query):
    class NoDatabase:
        @property
        def _lock(self):
            raise AssertionError("SQLite must not be touched")

    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoDatabase(), query)


def test_legitimate_unicode_title_query_keeps_existing_partial_contract(tmp_path):
    with closing(Storage(tmp_path / "unicode.sqlite")) as storage:
        page = search_public_notice_c(storage, "Éditions d'André")
        assert page.status == "partial"
        assert page.hits == ()
        assert page.known_matches == 0
