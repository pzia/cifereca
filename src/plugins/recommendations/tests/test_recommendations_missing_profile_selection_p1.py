"""#1599: Recommandations must not switch identity after a profile vanishes.

Synthetic GTK models and local overview notifications, no remote or real data.
"""
from types import SimpleNamespace as NS

import pytest

from tests.test_recommendations_ui import _identity_reload_view


def test_deleted_selected_profile_does_not_load_other_profile_from_overview():
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
    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None
    assert view._profile_selection_required is True
    assert view._reference_book is None
    assert view._reference_books == []
    assert view.plugin._pending_reference is None
    view.GLib.run_pending()
    assert refreshes == [None]


def test_missing_selection_is_sticky_across_later_overview_notifications():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    assert view.selected_identity_id() is None

    rows[:] = [
        {"identity_id": "third", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view._reload_identities()
    assert dropdown.selected == 0xFFFFFFFF
    assert view._profile_selection_required is True
    assert view.selected_identity_id() is None
    assert refreshes == []


def test_missing_profile_reappearance_requires_explicit_user_selection():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    rows[:] = [
        {"identity_id": "default", "selected": True},
        {"identity_id": "other", "selected": False},
    ]
    view._reload_identities()
    assert dropdown.selected == 0xFFFFFFFF
    assert view.selected_identity_id() is None
    dropdown.set_selected(0)  # Explicit profile choice in GTK.
    assert view._profile_selection_required is False
    assert view.selected_identity_id() == "default"
    assert refreshes == ["default"]


def test_manual_new_selection_remains_selected_after_profiles_reorder():
    rows = [{"identity_id": "default", "selected": True}]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")
    rows[:] = [{"identity_id": "other", "selected": True}]
    view._reload_identities()
    dropdown.set_selected(0)
    assert view.selected_identity_id() == "other"

    rows[:] = [
        {"identity_id": "third", "selected": False},
        {"identity_id": "other", "selected": True},
    ]
    view._reload_identities()
    assert dropdown.selected == 1
    assert view.selected_identity_id() == "other"
    assert view._profile_selection_required is False
    assert refreshes == ["other"]


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
