"""#1599: Recommandations must not switch identity after a profile vanishes.

Synthetic GTK models and local overview notifications, no remote or real data.
"""
from types import SimpleNamespace as NS

import pytest

from tests.test_recommendations_ui import _identity_reload_view


def test_core_global_switch_after_profile_deletion_clears_previous_context():
    rows = [
        {"identity_id": "default", "selected": True, "label": "Alice"},
        {"identity_id": "other", "selected": False, "label": "Bob"},
    ]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    old_book = object()
    view._reference_book = old_book
    view._reference_books = [old_book]
    view.plugin._pending_reference = old_book

    rows[:] = [{"identity_id": "other", "selected": True, "label": "Bob"}]
    view._identity_overview_changed()
    assert dropdown.selected == 0
    assert view.selected_identity_id() == "other"
    assert view._profile_selection_required is False
    assert view._reference_book is None
    assert view._reference_books == []
    assert view.plugin._pending_reference is None
    assert refreshes == ["other"]


def test_global_core_selection_is_authoritative_across_notifications():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    assert view.selected_identity_id() == "other"

    rows[:] = [
        {"identity_id": "third", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view._reload_identities()
    assert dropdown.selected == 0
    assert view._profile_selection_required is False
    assert view.selected_identity_id() == "third"
    assert refreshes == []


def test_core_reselection_of_previous_profile_does_not_require_plugin_choice():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    rows[:] = [
        {"identity_id": "default", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view._reload_identities()
    assert dropdown.selected == 0
    assert view.selected_identity_id() == "default"
    assert view._profile_selection_required is False
    assert refreshes == []


def test_core_selection_remains_correct_after_profile_reorder():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    assert view.selected_identity_id() == "other"

    rows[:] = [
        {"identity_id": "third", "selected": False},
        {"identity_id": "other", "selected": True},
    ]
    view._reload_identities()
    assert dropdown.selected == 1
    assert view.selected_identity_id() == "other"
    assert view._profile_selection_required is False
    assert refreshes == []


def test_absent_or_ambiguous_core_selection_never_falls_back():
    rows = [
        {"identity_id": "default", "selected": False},
        {"identity_id": "other", "selected": False},
    ]
    view, dropdown, _ = _identity_reload_view(rows, active_identity="default")
    view._reload_identities()
    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None

    rows[0]["selected"] = True
    rows[1]["selected"] = True
    view._reload_identities()
    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None


def test_empty_initial_model_may_select_explicit_host_default():
    rows = [{"identity_id": "first", "selected": True, "label": "First"}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity=None)
    view._identity_ids = []
    view._identity_model.items = []
    view._profile_selection_required = False
    view._reload_identities()
    assert view.selected_identity_id() == "first"
    assert dropdown.selected == 0
    assert view._profile_selection_required is False
    assert refreshes == []


@pytest.mark.parametrize("invalid", [None, -1, 0xFFFFFFFF, 9, True, "0"])
def test_invalid_gtk_indices_cannot_resolve_to_other_profile(invalid):
    rows = [{"identity_id": "default", "selected": True},
            {"identity_id": "other", "selected": False}]
    view, dropdown, _ = _identity_reload_view(rows, active_identity="default")
    view._reload_identities()
    dropdown.selected = invalid
    assert view.selected_identity_id() is None
