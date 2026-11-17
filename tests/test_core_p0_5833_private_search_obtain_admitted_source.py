"""P0 #5833: admitted private Search download survives GTK navigation only.

The canonical executor keeps exact source owner, session, SQLite binding,
source generation and a provider-attested current offer. No real network I/O.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from cifereca.core import selected_source_offer as selected
from tests.test_core_p0_5054_numilog_legacy_writer_retired import _item


class Source:
    selected_purchase_branch = "private-books"

    def downloadable_selected_delivery_candidate_guarded(self, **_kw):
        pytest.fail("passive admission cannot GET private source")


class Host:
    def __init__(self):
        self.reader = NS(identity_id="reader")
        self.account = Account(
            identity_id="reader", provider_id="numilog",
            credential_id="card", unique_id="borrower",
        )
        self.accounts = [self.account]
        self.session = Source()
        self._sessions = {("numilog", "card"): self.session}
        self._session_key = lambda *parts: parts
        self._account_context_epoch = 7
        self._source_context_epoch = 11
        self.poison_navigation = False

    @property
    def current_identity(self):
        if self.poison_navigation:
            pytest.fail("admitted worker read GTK navigation")
        return self.reader


@pytest.fixture
def rig(monkeypatch):
    host = Host()
    monkeypatch.setattr(selected, "has_complete_delivery_abi",
                        lambda _session: True)
    monkeypatch.setattr(
        selected, "exact_private_inventory_binding_valid",
        lambda _service, account, *, session, provider_id: (
            account is host.account and session is host.session
            and provider_id == "numilog"
        ),
    )
    return host


def test_captured_private_offer_survives_a_b_a_and_never_uses_navigation(
    rig, monkeypatch,
):
    item = _item()
    target = LibraryService.capture_admitted_private_search_offer(rig, item)
    assert type(target) is selected.SourceOfferTarget
    assert target.identity_id == "reader"
    assert target.account_id == "borrower"
    assert target.credential_id == "card"
    assert target.selection_epoch is None and target.source_epoch == 11
    invoked = []

    def canonical(service, admitted, *, resolve, right_current):
        assert service is rig and admitted is target
        assert resolve(service, None, "reader", "edition") == (target, None)
        assert right_current(service, target) is True
        invoked.append((admitted.identity_id, admitted.credential_id))
        return "completed-for-reader"

    from cifereca.core import explicit_owned_canonical
    monkeypatch.setattr(explicit_owned_canonical, "obtain_explicit_owned",
                        canonical)
    rig._account_context_epoch += 2  # A→B→A presentation navigation
    rig.poison_navigation = True
    assert LibraryService.obtain_admitted_private_search_offer(
        rig, target,
    ) == "completed-for-reader"
    assert invoked == [("reader", "card")]


@pytest.mark.parametrize("revoke", [
    "source_generation", "session_rebound", "account_removed",
    "account_duplicate", "binding_disabled",
])
def test_real_source_revocation_blocks_admitted_private_worker(
    rig, monkeypatch, revoke,
):
    target = LibraryService.capture_admitted_private_search_offer(rig, _item())
    if revoke == "source_generation":
        rig._source_context_epoch += 1
    elif revoke == "session_rebound":
        rig._sessions[("numilog", "card")] = Source()
    elif revoke == "account_removed":
        rig.accounts.clear()
    elif revoke == "account_duplicate":
        rig.accounts.append(rig.account)
    else:
        monkeypatch.setattr(
            selected, "exact_private_inventory_binding_valid",
            lambda *_a, **_kw: False,
        )
    rig.poison_navigation = True
    with pytest.raises(ValueError, match="selected_source_"):
        selected._resolve(rig, target)


@pytest.mark.parametrize("change", [
    {"owned": False}, {"downloadable": False},
    {"source_collection": "global"}, {"download_url": "https://private/?token=SECRET"},
    {"id": "different"}, {"metadata": {"search_provenance": "known_catalog_stale"}},
])
def test_inexact_private_search_item_never_becomes_an_offer(rig, monkeypatch, change):
    from cifereca.core import selected_search_admission
    admitted = Mock(side_effect=AssertionError("no private selection permitted"))
    monkeypatch.setattr(selected_search_admission, "select_source_offer_target",
                        admitted)
    with pytest.raises(ProviderConfigurationError, match="selected_search_source_unavailable"):
        LibraryService.capture_admitted_private_search_offer(
            rig, replace(_item(), **change),
        )
    admitted.assert_not_called()


def test_missing_private_session_does_not_downgrade_to_public_download(rig):
    rig._sessions.clear()
    with pytest.raises(ProviderConfigurationError, match="selected_search_source_unavailable"):
        LibraryService.capture_admitted_private_search_offer(rig, _item())


def test_explicit_private_execution_rejects_unadmitted_navigation_target(rig):
    old = selected.SourceOfferTarget(
        "reader", "numilog", "card", "borrower", "edition",
        "private-books", rig.session, selection_epoch=7,
    )
    with pytest.raises(ProviderConfigurationError, match="selected_search_source_unavailable"):
        LibraryService.obtain_admitted_private_search_offer(rig, old)


def test_real_direct_search_gtk_dequeue_keeps_pinned_service_and_source(monkeypatch):
    from pathlib import Path
    from tests.test_search_snapshot_refresh import _direct_callback_host
    window, invalidate = _direct_callback_host(monkeypatch)
    source = window.service
    private = object()
    result = NS(path=Path("/synthetic/private.epub"),
                acquisition=NS(action="download"))
    source.capture_admitted_private_search_offer = Mock(return_value=private)
    source.obtain_admitted_private_search_offer = Mock(return_value=result)
    source.obtain_search_item = Mock(side_effect=AssertionError("fallback GET"))
    window.download_direct_item(NS(source_provider="numilog"))
    assert len(window.tasks) == 1
    source.capture_admitted_private_search_offer.assert_called_once()
    # Navigation and service/widget rebinding before worker start are not a
    # new Core consent and must never redirect A to B.
    source.current_identity = NS(identity_id="bob")
    window._identity_context_generation += 1
    source.current_identity = NS(identity_id="alice")
    window._identity_context_generation += 1
    window.service = NS(
        current_identity=NS(identity_id="bob"),
        obtain_admitted_private_search_offer=Mock(
            side_effect=AssertionError("wrong service")
        ),
    )
    assert window.tasks[0].work() is result
    source.obtain_admitted_private_search_offer.assert_called_once_with(private)
    source.obtain_search_item.assert_not_called()
    window.tasks[0].done(result)
    invalidate.assert_not_called()


def test_gtk_failed_private_admission_never_enqueues_worker(monkeypatch):
    from tests.test_search_snapshot_refresh import _direct_callback_host
    window, _invalidator = _direct_callback_host(monkeypatch)
    source = window.service
    source.capture_admitted_private_search_offer = Mock(
        side_effect=ProviderConfigurationError("PRIVATE bearer")
    )
    source.obtain_search_item = Mock()
    window.show_error = Mock()
    window.download_direct_item(NS(source_provider="numilog"))
    assert not window.tasks
    source.obtain_search_item.assert_not_called()
    assert "PRIVATE" not in repr(window.show_error.call_args)


@pytest.mark.parametrize("observed", ["old-borrower", "", False, "borrower\n"])
def test_click_cannot_rebind_explicit_observed_account(rig, observed):
    with pytest.raises(ProviderConfigurationError, match="selected_search_source_unavailable"):
        LibraryService.capture_admitted_private_search_offer(
            rig, replace(_item(), source_account_id=observed),
        )


def test_click_preserves_matching_explicit_account(rig):
    target = LibraryService.capture_admitted_private_search_offer(
        rig, replace(_item(), source_account_id="borrower"),
    )
    assert target.account_id == "borrower" and target.source_epoch == 11


def _explicit_reader(rig):
    from cifereca.core.search_orchestration import capture_search_read_scope
    rig.identity_by_id = lambda key: rig.reader if key == "reader" else None
    rig._provider_enabled_for_identity = lambda provider, identity: (
        provider == "numilog" and identity == "reader"
    )
    rig.poison_navigation = True
    # Deliberately invalid navigation epoch: this client has no GTK lease.
    rig._account_context_epoch = "no navigation"
    return capture_search_read_scope(rig, "reader")


def test_confirmed_headless_offer_is_exact_and_single_attempt(rig, monkeypatch):
    from cifereca.core.selected_search_admission import capture_confirmed_selected_offer
    from cifereca.core import explicit_owned_canonical
    scope = _explicit_reader(rig)
    target = capture_confirmed_selected_offer(
        rig, _item(), reader_scope=scope, account_id="borrower", confirmed=True,
    )
    assert target.reader_scope is scope
    assert target.identity_id == "reader" and target.account_id == "borrower"
    assert target.selection_epoch is None and target.source_epoch == 11
    calls = []

    def canonical(service, admitted, *, resolve, right_current):
        assert service is rig and admitted is target
        assert resolve(service, None, "reader", "edition") == (target, None)
        assert right_current(service, target) is True
        calls.append(admitted)
        return "completed"

    monkeypatch.setattr(explicit_owned_canonical, "obtain_explicit_owned", canonical)
    assert LibraryService.obtain_admitted_private_search_offer(rig, target) == "completed"
    with pytest.raises(ValueError, match="confirmation_consumed"):
        LibraryService.obtain_admitted_private_search_offer(rig, target)
    assert calls == [target]


@pytest.mark.parametrize("confirmed,account_id", [
    (False, "borrower"), (1, "borrower"), (None, "borrower"),
    (True, "wrong"), (True, ""), (True, "borrower\n"),
])
def test_headless_admission_refuses_missing_confirmation_or_exact_account(
    rig, confirmed, account_id,
):
    from cifereca.core.selected_search_admission import capture_confirmed_selected_offer
    scope = _explicit_reader(rig)
    with pytest.raises(ValueError):
        capture_confirmed_selected_offer(
            rig, _item(), reader_scope=scope, account_id=account_id, confirmed=confirmed,
        )


@pytest.mark.parametrize("revoked", ["registry", "policy", "source", "core"])
def test_headless_mandate_revalidates_reader_and_source(rig, revoked):
    from cifereca.core.selected_search_admission import capture_confirmed_selected_offer
    scope = _explicit_reader(rig)
    target = capture_confirmed_selected_offer(
        rig, _item(), reader_scope=scope, account_id="borrower", confirmed=True,
    )
    service = rig
    if revoked == "registry":
        rig.reader = NS(identity_id="reader")
    elif revoked == "policy":
        rig._provider_enabled_for_identity = lambda *_: False
    elif revoked == "source":
        rig._source_context_epoch += 1
    else:
        service = Host()
    with pytest.raises(ValueError, match="selected_source_binding_changed"):
        selected._resolve(service, target)
