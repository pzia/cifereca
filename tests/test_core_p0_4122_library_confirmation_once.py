"""#4122 — a confirmed Library delivery is single-use, even on failure."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import explicit_owned_canonical as canonical
from cifereca.core import selected_source_offer as selection
from cifereca.core.owned_library_download import OwnedDownloadRejected
from cifereca.models import Account
from cifereca.services import LibraryService


@pytest.fixture
def case(monkeypatch):
    account = Account(identity_id="reader", provider_id="numilog",
                      credential_id="card-A", unique_id="account-A")

    class Session:
        selected_purchase_branch = "meslivres"

        def downloadable_selected_delivery_candidate_guarded(self, **kwargs):
            pytest.fail("Only the canonical executor can GET")

    session = Session()
    host = NS(accounts=[account], _sessions={("numilog", "card-A"): session},
              _session_key=lambda *scope: scope, _account_context_epoch=7,
              _source_context_epoch=11, current_identity=NS(identity_id="reader"))
    monkeypatch.setattr(selection, "has_complete_delivery_abi", lambda _: True)
    monkeypatch.setattr(selection, "exact_private_inventory_binding_valid",
                        lambda *_a, **_kw: True)
    entry = {"possessions": [{
        "provider_id": "numilog", "identity_id": "reader", "credential_id": "card-A",
        "account_id": "account-A", "item_id": "book-1", "kind": "owned", "state": "active",
    }]}
    passive = LibraryService.owned_library_download_target(host, entry)
    assert type(passive) is selection.SourceOfferTarget
    return host, passive


@pytest.mark.parametrize("failure", [False, True])
def test_confirmed_worker_cannot_replay_and_new_click_has_independent_permission(
    monkeypatch, case, failure,
):
    host, passive = case
    calls = []

    def execute(service, target, *, resolve, right_current):
        calls.append(target)
        assert resolve(service, None, "reader", "book-1") == (target, None)
        assert right_current(service, target)
        if failure:
            raise ValueError("synthetic delivery failure")
        return "received"

    monkeypatch.setattr(canonical, "obtain_explicit_owned", execute)
    target = LibraryService.capture_admitted_owned_library_offer(host, passive)
    # A copy of the worker's target shares the same single-use token.
    replay = replace(target)
    if failure:
        with pytest.raises(OwnedDownloadRejected):
            LibraryService.obtain_admitted_owned_library_offer(host, target)
    else:
        assert LibraryService.obtain_admitted_owned_library_offer(host, target) == "received"
    with pytest.raises(OwnedDownloadRejected):
        LibraryService.obtain_admitted_owned_library_offer(host, replay)
    assert len(calls) == 1
    fresh = LibraryService.capture_admitted_owned_library_offer(host, passive)
    assert fresh._confirmed_use is not target._confirmed_use
    if failure:
        with pytest.raises(OwnedDownloadRejected):
            LibraryService.obtain_admitted_owned_library_offer(host, fresh)
    else:
        assert LibraryService.obtain_admitted_owned_library_offer(host, fresh) == "received"
    assert len(calls) == 2


def test_headless_epoch_without_confirmation_cannot_reach_canonical_executor(monkeypatch, case):
    host, passive = case
    forged = replace(passive, selection_epoch=None, source_epoch=11)
    monkeypatch.setattr(canonical, "obtain_explicit_owned",
                        lambda *_a, **_kw: pytest.fail("missing click token"))
    with pytest.raises(OwnedDownloadRejected):
        LibraryService.obtain_admitted_owned_library_offer(host, forged)
    with pytest.raises(ValueError, match="selected_source_confirmation_required"):
        selection._resolve(host, forged)


def test_same_confirmation_is_atomic_across_concurrent_workers(monkeypatch, case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    host, passive = case
    target = LibraryService.capture_admitted_owned_library_offer(host, passive)
    barrier = Barrier(2)
    calls = []
    monkeypatch.setattr(canonical, "obtain_explicit_owned",
                        lambda *_a, **_kw: calls.append("execute") or "received")

    def run():
        barrier.wait(timeout=5)
        try:
            return LibraryService.obtain_admitted_owned_library_offer(host, replace(target))
        except OwnedDownloadRejected:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(run), pool.submit(run)
        assert sorted([first.result(timeout=5), second.result(timeout=5)]) == ["received", "refused"]
    assert calls == ["execute"]
