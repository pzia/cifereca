"""The exact account, not the GTK selector, drives explicit loan refreshes."""

from threading import RLock
from types import SimpleNamespace

import pytest

from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _service():
    alice = Account(unique_id="alice", provider_id="synthetic", credential_id="a", identity_id="alice")
    bob = Account(unique_id="bob", provider_id="synthetic", credential_id="b", identity_id="bob")
    calls = []
    service = SimpleNamespace(
        accounts=[alice, bob], current_account=bob, _account_context_epoch=1,
        _search_generation_lock=RLock(), last_loans=["unchanged"],
        _loans_for_account=lambda account, *, force: (
            calls.append((account, force)) or []
        ),
        diagnostics=SimpleNamespace(add=lambda *a, **k: None),
        refresh_adept_inventory=lambda identity_id: None,
    )
    return service, alice, bob, calls


def test_explicit_account_read_is_not_redirected_by_gtk_selection():
    service, alice, bob, calls = _service()
    assert LibraryService.refresh_loans(service, account=alice) == []
    assert calls == [(alice, False)]
    assert service.current_account is bob
    assert service.last_loans == ["unchanged"]


def test_legacy_no_arg_read_retains_selected_account_projection():
    service, _, bob, calls = _service()
    assert LibraryService.refresh_loans(service) == []
    assert calls == [(bob, False)]
    assert service.last_loans == []


def test_unregistered_account_cannot_request_private_loan_data():
    service, alice, _, calls = _service()
    impostor = Account(unique_id=alice.unique_id, provider_id=alice.provider_id,
                       credential_id=alice.credential_id, identity_id=alice.identity_id)
    with pytest.raises(ProviderConfigurationError, match="loan_account_scope_invalid"):
        LibraryService.refresh_loans(service, account=impostor)
    assert calls == []


def test_concurrent_gtk_switch_does_not_publish_old_explicit_result():
    service, alice, bob, calls = _service()

    def loans(account, *, force):
        calls.append((account, force))
        service.current_account = alice
        service._account_context_epoch += 1
        return ["source-results"]

    service._loans_for_account = loans
    assert LibraryService.refresh_loans(service, account=alice) == ["source-results"]
    assert service.last_loans == ["unchanged"]
    assert calls == [(alice, False)]
