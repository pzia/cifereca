"""Deferred identity loan reads keep admission and publication separate."""
from threading import RLock
from types import SimpleNamespace as NS

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def host():
    a = NS(provider_id="synthetic", credential_id="a", unique_id="a")
    b = NS(provider_id="synthetic", credential_id="a", unique_id="b")
    calls = []
    svc = NS(current_identity=NS(identity_id="alice"),
             _account_context_epoch=4, _source_context_epoch=9,
             _search_generation_lock=RLock(), last_loans=["old"])
    svc.accounts_for_identity = lambda identity: [a,b] if identity == "alice" else []
    svc._session_for_account = lambda account: NS(capabilities=NS(loans=True))
    def read(account, *, force, expected_source_epoch):
        calls.append((account.unique_id, expected_source_epoch))
        return [NS(source_provider="synthetic", source_credential="a",
                   source_account_id=account.unique_id, id="same",
                   due_at=None, title=account.unique_id)]
    svc._loans_for_account = read
    svc.refresh_adept_inventory = lambda identity: calls.append(("adept", identity))
    return svc,calls


def run(svc, identity="alice"):
    return LibraryService.refresh_identity_loans(
        svc, force=True, identity_id=identity,
        expected_source_epoch=9, publication_epoch=4,
    )


def test_navigation_aba_before_worker_keeps_reads_but_rejects_core_projection():
    svc,calls = host()
    svc._account_context_epoch = 6
    assert [r.title for r in run(svc)] == ["a", "b"]
    assert calls == [("a",9), ("b",9), ("adept","alice")]
    assert svc.last_loans == ["old"]


def test_revocation_before_worker_never_reads_provider_or_adept():
    svc,calls = host()
    svc._source_context_epoch = 10
    with pytest.raises(ProviderConfigurationError, match="loan_inventory_scope_changed"):
        run(svc)
    assert calls == []
    assert svc.last_loans == ["old"]


def test_revocation_during_first_read_prevents_next_account_and_projection():
    svc,calls = host()
    read = svc._loans_for_account
    def changed(*args, **kwargs):
        result = read(*args, **kwargs)
        svc._source_context_epoch += 1
        return result
    svc._loans_for_account = changed
    with pytest.raises(ProviderConfigurationError, match="loan_inventory_scope_changed"):
        run(svc)
    assert calls == [("a",9)]
    assert svc.last_loans == ["old"]


def test_missing_admitted_identity_does_not_fall_back_to_navigation():
    svc,calls = host()
    with pytest.raises(ValueError, match="loan_identity_required"):
        run(svc,None)
    assert calls == []
    assert svc.last_loans == ["old"]
