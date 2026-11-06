"""#4761/#4664: multi-catalogue filter also constrains in-panel source actions.

The test is pure/headless: no live provider, no GTK, no search or HTTP GET.
"""
from types import SimpleNamespace as NS

from recommendation_engine import RankedCandidate
from tests.test_recommendation_catalogue_navigation_p1 import (
    _view, _variant, ISBN_1, ISBN_2,
)


def _actions(detail, prefix):
    return [action for action in detail.actions if action.id.startswith(prefix)]


def test_changing_source_filter_rebuilds_actions_without_another_search():
    variants = (
        _variant(ISBN_1, provider="kobo", credential="vault-A", record="kobo-book"),
        _variant(ISBN_2, provider="paris", credential="card-B", record="paris-book"),
    )
    view, _, local_calls, _original_detail, row = _view(variants)
    ranked = view._ranked_by_key[row.key]
    view._ranked_by_key[row.key] = RankedCandidate(
        item=ranked.item, score=ranked.score, reasons=ranked.reasons,
        sources=("kobo", "paris"), notices=ranked.notices,
    )
    ranked = view._ranked_by_key[row.key]
    opened, presented, checked = [], [], []
    view.host = NS(
        search_books=lambda *_a, **_kw: local_calls.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: checked.append(kw) or True,
        open_loaded_search_notice=lambda **kw: opened.append(kw) or True,
    )
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert len(_actions(presented[-1], "recommendation.loaded-notice.")) == 2
    old_paris_action = _actions(
        presented[-1], "recommendation.loaded-notice.",
    )[1]
    assert local_calls == [] and opened == []

    selected = ["kobo"]
    view._query_controls = NS(selected_sources=lambda: tuple(selected))
    view._ranked = [ranked]
    view._matches = lambda _ranked: True
    view._candidate_key = lambda _ranked: row.key
    view._master.show_rows = lambda *_a, **_kw: None
    checked.clear()
    view._render(preserve_summary=True)

    assert len(presented) == 2
    kobo = presented[-1]
    assert len(_actions(kobo, "recommendation.loaded-notice.")) == 1
    assert checked and {c["provider"] for c in checked} == {"kobo"}
    assert _actions(kobo, "recommendation.search.isbn.")[0].id.endswith(ISBN_1)
    assert len(_actions(kobo, "recommendation.search.isbn.")) == 1
    assert old_paris_action.callback() is False

    old_kobo_action = _actions(kobo, "recommendation.loaded-notice.")[0]
    selected[:] = ["paris"]
    checked.clear()
    view._render(preserve_summary=True)
    assert len(presented) == 3
    paris = presented[-1]
    assert len(_actions(paris, "recommendation.loaded-notice.")) == 1
    assert {c["provider"] for c in checked} == {"paris"}
    assert _actions(paris, "recommendation.search.isbn.")[0].id.endswith(ISBN_2)
    assert len(_actions(paris, "recommendation.search.isbn.")) == 1
    assert old_kobo_action.callback() is False
    assert not opened and not local_calls


def test_invalid_active_source_filter_does_not_reveal_all_exact_routes():
    view, _, calls, _initial, row = _view((
        _variant(provider="kobo"),
        _variant(ISBN_2, provider="paris"),
    ))
    checked, presented = [], []
    view.host = NS(
        search_books=lambda *_a, **_kw: calls.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: checked.append(kw) or True,
        open_loaded_search_notice=lambda **kw: True,
    )
    view._query_controls = NS(selected_sources=lambda: {"not": "a multiselect"})
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert checked == []
    assert _actions(presented[-1], "recommendation.loaded-notice.") == []
    assert _actions(presented[-1], "recommendation.search.isbn.") == []
    assert calls == []
