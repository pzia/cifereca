"""#1599: invalid GTK selections must not silently borrow another profile.

Pure synthetic UI surfaces. No provider, filesystem, network or GTK runtime.
"""
from types import SimpleNamespace as NS

import pytest

from recommendations import UxRecommendationsView


@pytest.mark.parametrize("selected", (-1, 2, 4294967295))
def test_invalid_selection_never_falls_back_to_first_identity(selected):
    view = object.__new__(UxRecommendationsView)
    view._identity_ids = ["alice", "bob"]
    view._identity = NS(get_selected=lambda: selected)
    assert view.selected_identity_id() is None


def test_explicit_valid_profile_is_still_selected():
    view = object.__new__(UxRecommendationsView)
    view._identity_ids = ["alice", "bob"]
    view._identity = NS(get_selected=lambda: 1)
    assert view.selected_identity_id() == "bob"
    view._identity_ids = []
    assert view.selected_identity_id() is None


def test_missing_profile_aborts_pending_work_and_invalidates_stale_completion():
    cancelled, spinner, buttons, summary = [], [], [], []
    view = object.__new__(UxRecommendationsView)
    view._identity_ids = ["alice", "bob"]
    view._identity = NS(get_selected=lambda: 4294967295)
    view._reset_llm_selection = lambda key: None
    view._task_handle = NS(cancel=lambda: cancelled.append("cancelled"))
    view._generation = 9
    view._busy = True
    view._spinner = NS(stop=lambda: spinner.append("stopped"))
    view._refresh_button = NS(set_sensitive=buttons.append)
    view._summary = NS(set_label=summary.append)
    assert view._start_refresh() is False
    assert cancelled == ["cancelled"]
    assert view._task_handle is None
    assert view._generation == 10
    assert view._busy is False
    assert spinner == ["stopped"] and buttons == [True]
    assert summary == ["Aucun profil disponible."]
    # An old worker may already have queued this GTK callback.
    assert view._base_complete(9, None, None) is False


def test_lost_profile_discards_rows_before_showing_new_context():
    calls = []
    view = object.__new__(UxRecommendationsView)
    view._identity_ids = ["alice", "bob"]
    view._identity = NS(get_selected=lambda: -1)
    view._active_identity_id = "alice"
    view._ranked = ["a private prior-profile item"]
    view._ranked_by_key = {"private": object()}
    view._provisional_order = {}
    view._clear_overview_refresh = lambda: None
    view._set_provisional = lambda value: None
    view._diagnostic_tooltip = lambda: "safe"
    view._progress = NS(set_tooltip_text=lambda value: None)
    view._master = NS(
        show_rows=lambda rows, **kwargs: calls.append(("rows", rows)),
        set_list_state=lambda *a: calls.append(("state",)),
        empty_detail=lambda *a: calls.append(("empty",)),
    )
    view._summary = NS(set_label=lambda text: calls.append(("summary", text)))
    view._start_refresh = lambda: False
    view._busy = False
    assert view.refresh() is False
    assert view._active_identity_id is None
    assert view._ranked == [] and view._ranked_by_key == {}
    assert calls[0] == ("rows", ())
    assert ("empty",) in calls
