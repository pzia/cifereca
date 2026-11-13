"""A missing profile must not broaden an identity-scoped loan refresh."""

from threading import RLock
from types import SimpleNamespace

import pytest

from cifereca.services import LibraryService


def _service(active=None):
    calls = []
    instance = SimpleNamespace(
        current_identity=(
            SimpleNamespace(identity_id=active) if active is not None else None
        ),
        _account_context_epoch=1,
        _search_generation_lock=RLock(),
        accounts_for_identity=lambda identity_id: (
            calls.append(identity_id) or []
        ),
        last_loans=["unchanged"],
        refresh_adept_inventory=lambda identity_id: None,
    )
    return instance, calls


@pytest.mark.parametrize("identity_id", [None, "", "  ", 42])
def test_missing_or_invalid_identity_cannot_expand_to_all_profiles(identity_id):
    service, calls = _service(active=None)
    with pytest.raises(ValueError, match="loan_identity_required"):
        LibraryService.refresh_identity_loans(service, identity_id=identity_id)
    assert calls == []
    assert service.last_loans == ["unchanged"]


def test_explicit_identity_is_stable_without_any_gtk_selection():
    service, calls = _service(active=None)
    assert LibraryService.refresh_identity_loans(service, identity_id="alice") == []
    assert calls == ["alice"]


def test_omitted_identity_keeps_legacy_gtk_selection_for_compatibility():
    service, calls = _service(active="bob")
    assert LibraryService.refresh_identity_loans(service) == []
    assert calls == ["bob"]


def test_empty_explicit_identity_never_falls_back_to_active_gtk_selection():
    service, calls = _service(active="bob")
    with pytest.raises(ValueError, match="loan_identity_required"):
        LibraryService.refresh_identity_loans(service, identity_id="")
    assert calls == []
