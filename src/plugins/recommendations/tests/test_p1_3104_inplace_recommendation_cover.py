"""#3104: a selected recommendation's list and detail share the same cover.

The core list can repaint when a RankedCandidate.item is enriched *in place*,
but the plugin previously refreshed its detail only when the RankedCandidate
object identity, generation or catalogue selection changed.
"""
from __future__ import annotations

from .recommendations_catalogue_test_fixtures import _variant, _view


PUBLIC_COVER = (
    "https://covers.feedbooks.net/item/synthetic-id_12345.jpg"
    "?size=medium&t=1762000000"
)


def _loaded_view():
    view, identity, searches, _initial, row = _view((_variant(),))
    ranked = view._ranked_by_key[row.key]
    view._ranked = [ranked]
    view._matches = lambda _candidate: True
    view._candidate_key = lambda _candidate: row.key
    views, details = [], []
    view._master.show_rows = lambda rows, **_kw: views.append(tuple(rows))
    view._master.show_detail = details.append
    view._render(preserve_summary=True)
    assert len(views[-1]) == 1
    assert view._exact_notice_state["ranked"] is ranked
    assert views[-1][0].thumbnail_url is None
    assert details[-1].thumbnail_url is None
    return view, ranked, views, details, searches


def test_same_ranked_object_enriched_image_updates_both_surfaces():
    view, ranked, views, details, searches = _loaded_view()
    baseline_details = len(details)
    baseline_generation = view._generation
    initial_state = view._exact_notice_state
    ranked.item.thumbnail_url = PUBLIC_COVER  # unchanged object and SourceKey
    view._render(preserve_summary=True)
    assert views[-1][0].key == views[-2][0].key
    assert views[-1][0].thumbnail_url == PUBLIC_COVER
    assert len(details) == baseline_details + 1
    assert details[-1].thumbnail_url == PUBLIC_COVER
    assert view._exact_notice_state is initial_state
    assert view._exact_notice_state["row_presentation"] == (
        views[-1][0].title, views[-1][0].subtitle, PUBLIC_COVER,
        None,
    )
    assert view._generation == baseline_generation
    assert searches == []
    view._render(preserve_summary=True)
    assert len(details) == baseline_details + 1  # unchanged render is stable


def test_same_ranked_object_cover_revocation_repaints_detail_without_old_image():
    view, ranked, views, details, searches = _loaded_view()
    ranked.item.thumbnail_url = PUBLIC_COVER
    view._render(preserve_summary=True)
    ranked.item.thumbnail_url = None
    view._render(preserve_summary=True)
    assert views[-1][0].thumbnail_url is None
    assert details[-1].thumbnail_url is None
    assert len(details) == 3  # initial state, new image, revocation
    assert searches == []


def test_same_ranked_object_bibliography_change_refreshes_selected_detail():
    view, ranked, views, details, searches = _loaded_view()
    ranked.item.title = "Nouvelle présentation exacte"
    ranked.item.author = "Nouvelle autrice"
    view._render(preserve_summary=True)
    assert views[-1][0].title == details[-1].title
    assert views[-1][0].subtitle == details[-1].subtitle
    assert searches == []


def test_inplace_cover_does_not_resurrect_stale_detail_while_ranking_busy():
    view, ranked, views, details, searches = _loaded_view()
    previous_count = len(details)
    view._generation += 1
    view._busy = True
    view._reset_llm_selection(None)  # old actions revoked for new generation
    ranked.item.thumbnail_url = PUBLIC_COVER
    view._render(preserve_summary=True)
    assert views[-1][0].thumbnail_url == PUBLIC_COVER
    assert len(details) == previous_count  # no stale detail/actions during load
    assert view._exact_notice_state["generation"] != view._generation
    assert searches == []

    view._busy = False
    view._render(preserve_summary=True)
    assert len(details) == previous_count + 1
    assert details[-1].thumbnail_url == PUBLIC_COVER
    assert view._exact_notice_state["generation"] == view._generation
    assert searches == []
