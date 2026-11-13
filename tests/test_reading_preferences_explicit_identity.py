"""Reading preference operations are scoped by explicit identity, not GTK state."""

from types import SimpleNamespace

import pytest

from cifereca.services import LibraryService
from cifereca.core import explicit_reading_preferences as preferences


def _service(*, selected="bob", books=()):
    storage = object()
    return SimpleNamespace(
        current_identity=SimpleNamespace(identity_id=selected),
        storage=storage,
        identity_by_id=lambda key: (
            SimpleNamespace(identity_id=key) if key in {"alice", "bob"} else None
        ),
        library_entries=lambda *, identity_id, scan: list(books),
    )


def test_snapshot_uses_requested_identity_when_another_is_displayed(monkeypatch):
    calls = []
    monkeypatch.setattr(preferences, "preference_snapshot",
                        lambda storage, identity_id: calls.append(identity_id) or "snapshot")
    service = _service()

    assert LibraryService.reading_preference_snapshot(
        service, identity_id="alice",
    ) == "snapshot"
    assert calls == ["alice"]


def test_write_keeps_exact_owner_confirmation_and_cas(monkeypatch):
    calls = []
    monkeypatch.setattr(
        preferences, "change_preference",
        lambda storage, identity_id, **kwargs: (
            calls.append((identity_id, kwargs)) or "written"
        ),
    )
    service = _service(books=({"identity_id": "alice", "key": "book-1"},))

    assert LibraryService.change_reading_preference(
        service, identity_id="alice", library_key="book-1",
        preference="liked", expected_revision=4, user_confirmed=True,
    ) == "written"
    assert calls == [("alice", {
        "library_key": "book-1", "preference": "liked",
        "expected_revision": 4, "user_confirmed": True, "clear_all": False,
    })]


def test_unknown_identity_and_missing_confirmation_fail_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(preferences, "change_preference",
                        lambda *args, **kwargs: calls.append(kwargs))
    service = _service()

    with pytest.raises(ValueError, match="identity_unknown"):
        LibraryService.change_reading_preference(
            service, identity_id="unknown", expected_revision=0,
            user_confirmed=True, clear_all=True,
        )
    with pytest.raises(ValueError, match="confirmation_required"):
        LibraryService.change_reading_preference(
            service, identity_id="alice", expected_revision=0,
            user_confirmed=False, clear_all=True,
        )
    assert not calls


def test_other_identity_book_cannot_be_accepted(monkeypatch):
    calls = []
    monkeypatch.setattr(preferences, "change_preference",
                        lambda *args, **kwargs: calls.append(kwargs))
    service = _service(books=({"identity_id": "bob", "key": "book-1"},))

    with pytest.raises(ValueError, match="target_unverified"):
        LibraryService.change_reading_preference(
            service, identity_id="alice", library_key="book-1",
            preference="liked", expected_revision=0, user_confirmed=True,
        )
    assert not calls
