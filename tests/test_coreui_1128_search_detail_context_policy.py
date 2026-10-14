"""Headless callback context contract for search details (#1128)."""

from cifereca.gui.search_detail_context import detail_callback_is_current


BASE = dict(closed=False, selected_id="book-A", expected_id="book-A", captured=(3, 7, 11, "identity-A"), current=(3, 7, 11, "identity-A"))


def test_current_context_accepted():
    assert detail_callback_is_current(**BASE)


def test_closed_window_and_wrong_selection_rejected():
    assert not detail_callback_is_current(**{**BASE, "closed": True})
    assert not detail_callback_is_current(**{**BASE, "selected_id": "book-B"})


def test_identity_and_generations_must_match():
    for current in ((3, 7, 11, "identity-B"), (2, 7, 11, "identity-A"), (3, 6, 11, "identity-A"), (3, 7, 10, "identity-A")):
        assert not detail_callback_is_current(**{**BASE, "current": current})


def test_legacy_sync_caller_without_context_retains_compatibility():
    assert detail_callback_is_current(**{**BASE, "captured": None, "current": None})
    assert not detail_callback_is_current(**{**BASE, "closed": True, "captured": None})
