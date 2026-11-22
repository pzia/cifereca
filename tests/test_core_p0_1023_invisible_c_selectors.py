"""#1023: hidden controls in C source/format selectors cannot yield false zero."""
from contextlib import closing

import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage


class NoDatabase:
    @property
    def _lock(self):
        raise AssertionError("invalid filters must be refused before SQLite")


@pytest.mark.parametrize("options", [
    {"provider_ids": ("bnf\u200b",)},
    {"provider_ids": ("bnf\u202e",)},
    {"provider_ids": ("bnf\x00",)},
    {"content_types": ("EP\u200bUB",)},
    {"content_types": ("EP\u2066UB",)},
    {"content_types": ("PDF\n",)},
])
def test_invisible_selectors_fail_closed_before_search(options):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoDatabase(), "Cuisine", **options)


def test_known_c_search_keeps_normal_provider_and_format_filters(tmp_path):
    with closing(Storage(tmp_path / "filtered.sqlite")) as db:
        page = search_public_notice_c(
            db, "Éditions françaises", provider_ids=("bnf",),
            content_types=("EPUB",),
        )
        assert page.status == "partial"
        assert page.known_matches == 0
        assert page.hits == ()
