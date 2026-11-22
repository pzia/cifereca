"""#6397: absence of a book cover is not absence of its exact web sheet.

The observation and cover field are synthetic native facts; no supplier I/O.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_web_references import FIELD, qualified_public_web_source


def source(*, status, cover_state, candidates=(), thumbnail=None, inventory=True):
    fields = {"thumbnail_url": thumbnail, "public_bibliography": {"record_id": "book-1"},
              FIELD: {"schema": 1, "links": [{"url": "https://books.example/notice/1",
                                              "evidence": "provider.public_book"}]}}
    obs = NS(key=NS(visibility="public", branch_id="search.public.catalogue"),
             parser_id="core.search.public-cover", parser_version="2",
             status=status, fields_copy=lambda: {"search_result": fields})
    attr = NS(field_path=("search_result", "thumbnail_url"),
              state=cover_state, candidates=candidates)
    return NS(observation=obs, attribute=attr), inventory


def test_cover_proven_missing_still_retains_independent_web_reference():
    src, complete = source(status="complete", cover_state="missing")
    assert qualified_public_web_source(src, inventory_complete=complete)


@pytest.mark.parametrize("change", [
    {"candidates": ("unexpected",)},
    {"thumbnail": "https://books.example/cover.jpg"},
    {"status": "partial"},
    {"inventory": False},
])
def test_missing_cover_must_be_an_exact_complete_negative(change):
    kwargs = dict(status="complete", cover_state="missing")
    kwargs.update(change)
    src, complete = source(**kwargs)
    assert not qualified_public_web_source(src, inventory_complete=complete)


def test_existing_available_and_partial_pending_branches_remain_distinct():
    full, _ = source(status="complete", cover_state="available", candidates=("attested",))
    partial, _ = source(status="partial", cover_state="pending")
    assert qualified_public_web_source(full, inventory_complete=True)
    assert qualified_public_web_source(partial, inventory_complete=True)
    missing, _ = source(status="complete", cover_state="pending")
    assert not qualified_public_web_source(missing, inventory_complete=True)
