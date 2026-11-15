"""P1 #4761 — GTK reference chooser refuses transient/poisoned positions.

A model replacement may expose Gtk.INVALID_LIST_POSITION. That notification is
not the user's explicit "Bibliothèque entière" selection and must not replace
the reference, recompute recommendations, or silently enable opt-in controls.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from recommendations import UxRecommendationsView


def _view():
    books = [{"title": "Premier"}, {"title": "Second"}]
    selected = [2]
    events = []
    view = object.__new__(UxRecommendationsView)
    view._reloading_references = False
    view._reference_books = books
    view._reference_book = books[1]
    view.plugin = SimpleNamespace(_pending_reference=books[1])
    view._reference = SimpleNamespace(get_selected=lambda: selected[0])
    view._clear_preference_optin = lambda: events.append("clear-preferences")
    view._clear_local_only_optin = lambda: events.append("clear-local-only")
    view._preference_optin = SimpleNamespace(
        set_sensitive=lambda value: events.append(("preferences-sensitive", value)),
    )
    view._local_only_optin = SimpleNamespace(
        set_sensitive=lambda value: events.append(("local-only-sensitive", value)),
    )
    view.refresh = lambda: events.append("refresh")
    return view, books, selected, events


@pytest.mark.parametrize("invalid", (-1, 0xFFFFFFFF, True, False, 1.0, "1", None, 3))
def test_invalid_gtk_reference_position_does_not_clear_or_select_another_book(invalid):
    view, books, selected, events = _view()
    selected[0] = invalid
    view._reference_changed()
    assert view._reference_book is books[1]
    assert view.plugin._pending_reference is books[1]
    assert events == []


def test_explicit_all_and_valid_reference_selection_still_recalculate_once():
    view, books, selected, events = _view()
    selected[0] = 0
    view._reference_changed()
    assert view._reference_book is None
    assert view.plugin._pending_reference is None
    assert events == [
        "clear-preferences", "clear-local-only",
        ("preferences-sensitive", True), ("local-only-sensitive", True),
        "refresh",
    ]

    events.clear()
    selected[0] = 1
    view._reference_changed()
    assert view._reference_book is books[0]
    assert view.plugin._pending_reference is books[0]
    assert events == [
        "clear-preferences", "clear-local-only",
        ("preferences-sensitive", False), ("local-only-sensitive", False),
        "refresh",
    ]
    events.clear()
    view._reference_changed()
    assert events == []


def test_reference_model_refresh_guard_still_blocks_notifications():
    view, books, selected, events = _view()
    view._reloading_references = True
    selected[0] = 0
    view._reference_changed()
    assert view._reference_book is books[1]
    assert view.plugin._pending_reference is books[1]
    assert events == []
