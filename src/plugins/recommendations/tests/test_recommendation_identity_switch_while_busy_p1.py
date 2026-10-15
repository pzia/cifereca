"""#1599: switching identity revokes in-flight recommendation projections immediately.

Headless GTK dropdown stubs; no network, account data, filesystem or LLM.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendations import UxRecommendationsView
from tests.test_recommendations_ui import _identity_reload_view


def _busy_view(rows):
    view, dropdown, refreshes = _identity_reload_view(
        rows, active_identity="default", busy=True,
    )
    cancelled = []
    rendered = []
    visible_labels = []
    view._generation = 41
    view._task_handle = NS(cancel=lambda: cancelled.append("old-profile-worker"))
    view._ranked = ["PRIVATE_OLD_IDENTITY_RECOMMENDATION"]
    view._ranked_by_key = {"old": "PRIVATE_OLD_IDENTITY_RECOMMENDATION"}
    view._provisional_order = {"old": 1}
    view._set_provisional = lambda flag: None
    view._diagnostic_tooltip = lambda: "cache status"
    view._progress = NS(set_tooltip_text=lambda value: None)
    view._master = NS(
        show_rows=lambda books, **kwargs: rendered.append(("rows", books)),
        empty_detail=lambda *args: rendered.append(("detail-empty", args)),
        set_list_state=lambda *args: rendered.append(("state", args)),
    )
    view._summary = NS(set_label=visible_labels.append)
    view._spinner = NS(stop=lambda: None)
    view._refresh_button = NS(set_sensitive=lambda enabled: None)
    view._reset_llm_selection = lambda key: None
    # Exercise the real refresh -> _start_refresh cancellation and generation
    # path, but stop before submitting any worker (plugin.context is None).
    view.refresh = lambda: UxRecommendationsView.refresh(view)
    return view, dropdown, refreshes, cancelled, rendered, visible_labels


def test_core_switch_while_busy_cancels_old_worker_before_its_completion():
    rows = [
        {"identity_id": "default", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view, dropdown, refreshes, cancelled, rendered, labels = _busy_view(rows)

    rows[0]["selected"] = False
    rows[1]["selected"] = True
    view._identity_overview_changed()

    assert view.selected_identity_id() == "other"
    assert view._active_identity_id == "other"
    assert cancelled == ["old-profile-worker"]
    assert view._generation == 42
    assert view._task_handle is None
    assert not view._busy
    assert view._ranked == []
    assert view._ranked_by_key == {}
    assert rendered[0] == ("rows", ())
    assert any(entry[0] == "detail-empty" for entry in rendered)
    assert view._base_complete(41, None, None) is False
    assert not view._overview_refresh_pending
    assert view.GLib.sources == {}
    assert refreshes == []
    assert labels[-1] == "Plugin non activé."


def test_disappearing_profile_while_busy_clears_rows_without_selecting_other():
    rows = [
        {"identity_id": "default", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view, dropdown, _, cancelled, rendered, labels = _busy_view(rows)
    rows[:] = [{"identity_id": "other", "selected": True}]

    view._identity_overview_changed()

    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None
    assert view._profile_selection_required is True
    assert view._active_identity_id is None
    assert cancelled == ["old-profile-worker"]
    assert view._generation == 42
    assert view._ranked == []
    assert rendered[0] == ("rows", ())
    assert view._base_complete(41, None, None) is False
    assert labels[-1] == "Aucun profil disponible."
    assert not view._overview_refresh_pending


def test_overview_update_same_profile_still_coalesces_during_current_worker():
    rows = [
        {"identity_id": "default", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view, _, _, cancelled, rendered, _ = _busy_view(rows)

    view._identity_overview_changed()

    assert view.selected_identity_id() == "default"
    assert view._active_identity_id == "default"
    assert cancelled == []
    assert view._generation == 41
    assert view._busy is True
    assert view._overview_refresh_pending is True
    assert view.GLib.sources == {}
    assert rendered == []


def test_last_profile_removed_while_busy_immediately_revokes_private_result():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, _, cancelled, rendered, labels = _busy_view(rows)
    rows.clear()

    view._identity_overview_changed()

    assert view._identity_ids == []
    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None
    assert view._active_identity_id is None
    assert cancelled == ["old-profile-worker"]
    assert view._generation == 42
    assert view._ranked == []
    assert rendered[0] == ("rows", ())
    assert view._base_complete(41, None, None) is False
    assert labels[-1] == "Aucun profil disponible."
    assert not view._overview_refresh_pending
