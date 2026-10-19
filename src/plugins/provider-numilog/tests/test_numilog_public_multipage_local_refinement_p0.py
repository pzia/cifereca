"""#1886 P0: local refinement across multiple *already loaded* Numilog pages.

Only the observed anonymous 'jefferson' fixture is used. Two pages are
synthesized by partitioning its 20 distinct real cards and re-attesting their
source page URLs. This does NOT claim Numilog returned page 2 live.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_multi_refinement import refine_loaded_public_pages
from cifereca_numilog_public_local_filter import refine_loaded_public_page
import cifereca_numilog_public_provider as public_provider

HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"
PRIVATE_MARKER = "ALICE_PRIVATE_SIGNED_SESSION"


def _relabel(row, *, url, index):
    """Synthetic page split preserving original product IDs and HTML selectors."""
    meta = dict(row.metadata)
    meta["source_url"] = url
    meta["page_index"] = index
    meta["field_evidence"] = {
        field: {**proof, "source_url": url}
        for field, proof in meta["field_evidence"].items()
    }
    return replace(row, metadata=meta, source_provider="numilog-catalog")


def two_pages(*, total=20):
    source = parse_public_search(HTML, URL)
    next_url = source.next_url
    assert next_url and "GRID_PAGE=1" in next_url
    first = replace(
        source,
        items=tuple(_relabel(row, url=URL, index=0)
                    for row in source.items[:10]),
        total_results=total, page_index=0, next_url=next_url, complete=False,
        # The original 242-result HTML has links to later pages. This test
        # synthesizes a *two*-page 20-result snapshot, not the live pager.
        pagination_links=({"label": "2", "url": next_url, "GRID_PAGE": "1"},),
    )
    second = replace(
        source,
        items=tuple(_relabel(row, url=next_url, index=1)
                    for row in source.items[10:]),
        total_results=total, page_index=1, next_url=None,
        pagination_links=(), complete=False,
    )
    return first, second


def test_filter_across_two_loaded_pages_preserves_page_scope_and_rights():
    pages = two_pages()
    result = refine_loaded_public_pages(pages, title="JEFFERSON")
    first = refine_loaded_public_page(pages[0], title="jefferson")
    second = refine_loaded_public_page(pages[1], title="jefferson")
    assert first.matching_count > 0 and second.matching_count > 0
    assert result.matching_count == first.matching_count + second.matching_count
    assert result.loaded_page_indices == (0, 1)
    assert result.loaded_page_count == 2
    assert result.loaded_count == 20
    assert result.public_unfiltered_total == 20
    assert result.scope == "loaded_public_pages_only"
    assert result.applied_fields == ("title",)
    assert result.public_results_fully_loaded is True
    assert result.has_uninspected_or_unverified_matches is False
    assert all(row.source_provider == "numilog-catalog"
               and row.source_credential == "__public__"
               and row.source_collection == "public_catalog"
               and row.owned is False and row.downloadable is False
               and row.download_url is None for row in result.items)


def test_partial_multiple_pages_do_not_invent_exhaustive_global_results():
    p, q = two_pages(total=242)
    result = refine_loaded_public_pages((p, q), title="nobody-has-this-title")
    assert result.matching_count == 0
    assert result.missing_evidence_count == 0
    assert result.loaded_count == 20
    assert result.public_unfiltered_total == 242
    assert result.public_results_fully_loaded is False
    assert result.has_uninspected_or_unverified_matches is True
    one = refine_loaded_public_pages((p,), title="nobody-has-this-title")
    assert one.loaded_page_count == 1
    assert one.loaded_count == 10
    assert one.has_uninspected_or_unverified_matches is True


def test_missing_collection_is_counted_unknown_not_discarded_or_globally_absent():
    p, q = two_pages()
    result = refine_loaded_public_pages([p, q], collection="Romans Junior")
    assert result.matching_count == 2
    assert result.missing_evidence_count == 7
    assert result.public_unfiltered_total == 20
    assert result.public_results_fully_loaded is True
    assert result.has_uninspected_or_unverified_matches is True


def test_earlier_page_forward_pager_defeats_false_exhaustiveness():
    p, q = two_pages()
    future = URL + "&GRID_PAGE=2"
    first = replace(p, pagination_links=(*p.pagination_links, {
        "label": "3", "url": future, "GRID_PAGE": "2",
    }))
    result = refine_loaded_public_pages((first, q), title="Jefferson")
    assert result.loaded_count == result.public_unfiltered_total == 20
    assert result.public_results_fully_loaded is False
    assert result.has_uninspected_or_unverified_matches is True


def test_matched_rows_are_copy_on_return():
    p, q = two_pages()
    rows = refine_loaded_public_pages([p, q], collection="Romans Junior")
    rows.items[0].metadata["collection"] = "TAMPERED"
    again = refine_loaded_public_pages([p, q], collection="Romans Junior")
    assert again.matching_count == 2
    assert all(r.metadata["collection"] == "Romans Junior" for r in again.items)


@pytest.mark.parametrize("modify", [
    lambda a, b: (b, a),  # page indices must start at zero and be consecutive
    lambda a, b: (a, replace(b, page_index=2)),
    lambda a, b: (a, replace(b, total_results=21)),
    lambda a, b: (a, replace(b, total_results=True)),
    lambda a, b: (a, replace(b, items=(
        _relabel(a.items[0], url=b.items[0].metadata["source_url"], index=1),
        *b.items[1:],
    ))),  # duplicate product across pages
    lambda a, b: (replace(a, next_url=None), b),
    lambda a, b: (replace(a, next_url=URL + "&GRID_PAGE=2"), b),
    lambda a, b: (a, replace(b, next_url=URL + "&GRID_PAGE=3")),
    lambda a, b: (a, replace(b, pagination_links=({"url": URL.replace(
        "jefferson", "different-query")},))),
])
def test_contradictory_pages_fail_closed_with_fixed_code(modify):
    first, second = modify(*two_pages())
    with pytest.raises(ProviderCapabilityError, match="unverified_loaded_pages") as exc:
        refine_loaded_public_pages([first, second], title="Jefferson")
    assert PRIVATE_MARKER not in str(exc.value)


def test_cross_query_even_with_same_total_and_correct_pager_is_rejected():
    a, b = two_pages()
    other = b.items[0]
    new_url = b.items[0].metadata["source_url"].replace(
        "query=jefferson", "query=alice-private-session"
    )
    forged = _relabel(other, url=new_url, index=1)
    b = replace(b, items=(forged, *b.items[1:]))
    with pytest.raises(ProviderCapabilityError, match="unverified_loaded_pages"):
        refine_loaded_public_pages([a, b], title="Jefferson")


@pytest.mark.parametrize("pages", [
    (), [], [None], ("not a page",), "PRIVATE", {},
    [1] * 9,
])
def test_bad_page_collections_rejected_without_network(pages):
    with pytest.raises(ProviderCapabilityError, match="unverified_loaded_pages"):
        refine_loaded_public_pages(pages, author="Mourlevat")


def test_single_verified_zero_result_page_remains_exhaustive():
    original, _ = two_pages()
    empty = replace(original, items=(), total_results=0, complete=True,
                    next_url=None, pagination_links=())
    result = refine_loaded_public_pages((empty,), title="anything")
    assert result.loaded_count == result.matching_count == 0
    assert result.public_unfiltered_total == 0
    assert result.public_results_fully_loaded is True
    assert result.has_uninspected_or_unverified_matches is False


def test_one_unqualified_page_does_not_gain_full_coverage_from_a_counter():
    first, _ = two_pages()
    misleading = replace(first, total_results=10, next_url=None,
                         complete=False, pagination_links=())
    result = refine_loaded_public_pages((misleading,), author="nobody")
    assert result.loaded_count == result.public_unfiltered_total == 10
    assert result.public_results_fully_loaded is False
    assert result.has_uninspected_or_unverified_matches is True


def test_plugin_session_exposes_opt_in_offline_aggregate_without_http(monkeypatch):
    p, q = two_pages()
    monkeypatch.setattr(
        public_provider, "fetch_public_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("NO_HTTP")),
    )
    session = public_provider.PLUGIN.create_session(
        credential_id="__public__", label="public", credentials={}, options={},
        config={}, storage=None, diagnostics=None,
    )
    session.connect()
    try:
        result = session.refine_loaded_pages([p, q], publisher="Gallimard Jeunesse")
        assert result.loaded_count == 20
        assert result.loaded_page_count == 2
        assert result.scope == "loaded_public_pages_only"
    finally:
        session.close()
    with pytest.raises(ProviderError, match="non connecté"):
        session.refine_loaded_pages([p, q], title="Jefferson")
