"""P1 #4664/#3687: local filters cannot retain actions for a hidden book.

Reproduce a GTK list-model refresh that does not emit selection-changed.
No live GTK, provider GET or mutation is needed.
"""
from types import SimpleNamespace as NS

from recommendation_engine import RankedCandidate
from tests.test_recommendation_catalogue_navigation_p1 import _view, _variant


def test_nonempty_local_filter_revokes_hidden_selection_and_old_actions():
    view, _, searches, original, selected_row = _view((_variant(),))
    old_action = next(
        action for action in original.actions
        if action.id.startswith("recommendation.search.isbn.")
    )
    selected = view._ranked_by_key[selected_row.key]
    remaining = RankedCandidate(
        item=NS(
            title="Autre livre", author="Autre auteur", format="EPUB",
            published_at=None, description=None, friendly_url=None,
            thumbnail_url=None, cover_url=None,
        ),
        score=78, reasons=("Autre sujet",), sources=("kobo",),
        notices=(_variant(record="other-record"),),
    )
    view._ranked = [selected, remaining]
    view._matches = lambda ranked: ranked is remaining
    view._candidate_key = lambda ranked: (
        selected_row.key if ranked is selected else "remaining-book"
    )

    # The actual shared GTK list can replace its model without emitting a
    # new selected-row callback. The plugin must clear its own old detail.
    shown_rows, cleared, presentations = [], [], []
    view._master.show_rows = lambda rows, **kwargs: shown_rows.append(rows)
    view._master.empty_detail = lambda *args: cleared.append(args)
    view._master.show_detail = presentations.append

    view._render(preserve_summary=True)

    assert [row.key for row in shown_rows[-1]] == ["remaining-book"]
    assert len(cleared) == 1
    assert view._exact_notice_state is None
    assert view._llm_selection_key is None
    assert old_action.callback() is False
    assert searches == []

    # A user can explicitly select the remaining book: fresh actions are
    # generated, and only the ordinary local Search navigation is available.
    view._selected_row(shown_rows[-1][0])
    assert len(presentations) == 1
    fresh_action = next(
        action for action in presentations[-1].actions
        if action.id.startswith("recommendation.search.isbn.")
    )
    assert fresh_action.callback() is True
    assert len(searches) == 1


def test_filter_refresh_does_not_erase_new_synchronous_gtk_selection():
    view, _, searches, original, selected_row = _view((_variant(),))
    old_ranked = view._ranked_by_key[selected_row.key]
    remaining = RankedCandidate(
        item=NS(title="Autre livre", author="Autre auteur"),
        score=85, reasons=("Autre sujet",), sources=("kobo",),
        notices=(_variant(record="other-record"),),
    )
    view._ranked = [old_ranked, remaining]
    view._matches = lambda ranked: ranked is remaining
    view._candidate_key = lambda ranked: (
        selected_row.key if ranked is old_ranked else "remaining-book"
    )
    cleared, presentations = [], []
    view._master.empty_detail = lambda *args: cleared.append(args)
    view._master.show_detail = presentations.append

    def gtk_selects_remaining(rows, **kwargs):
        view._ranked_by_key = {"remaining-book": remaining}
        view._selected_row(rows[0])

    view._master.show_rows = gtk_selects_remaining
    view._render(preserve_summary=True)

    assert view._exact_notice_state["key"] == "remaining-book"
    assert len(presentations) == 1
    assert cleared == []
    assert searches == []
