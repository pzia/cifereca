"""P0 #5054/#3196: retired Kobo entitlement downloads are not Core paths.

Selected-source offers are exact and provider-neutral. Historical receipt,
backup, entitlement and file-name evidence is never executable authority.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import owned_library_download as action
from cifereca.core import selected_source_offer as selection


@pytest.mark.parametrize("provider", ["kobo", "numilog", "future-store"])
def test_only_exact_generic_selected_origin_is_offered(monkeypatch, provider):
    calls = []
    sentinel = object()
    monkeypatch.setattr(
        selection, "select_library_source_offer_target",
        lambda service, entry: calls.append((service, entry)) or sentinel,
    )
    service = NS()
    entry = {"provider_id": provider, "state": "remote_owned"}
    assert action.owned_library_download_target(service, entry) is sentinel
    assert calls == [(service, entry)]


@pytest.mark.parametrize("legacy", [
    None,
    {},
    {"provider_id": "kobo", "entitlement_id": "historical-right"},
    NS(entitlement_id="historical-right", identity_id="alice", provider_id="kobo"),
    NS(entitlement_id="historical-right", identity_id="alice", provider_id="future-store"),
])
def test_legacy_right_target_refused_before_any_download(monkeypatch, legacy):
    monkeypatch.setattr(
        selection, "obtain_source_offer",
        lambda *_args: pytest.fail("no legacy target can call canonical executor"),
    )
    with pytest.raises(action.OwnedDownloadRejected, match="owned_download_unavailable"):
        action.obtain_owned_library_item(NS(), legacy)


def test_typed_exact_selected_source_dispatches_one_canonical_offer(monkeypatch):
    from cifereca.core.selected_source_offer import SourceOfferTarget
    target = SourceOfferTarget(
        identity_id="alice", provider_id="future-store",
        credential_id="credential-A", account_id="account-A",
        item_id="edition-1", branch="selected", session=NS(),
    )
    service = NS()
    outcome = object()
    calls = []
    monkeypatch.setattr(
        selection, "obtain_source_offer",
        lambda received_service, received_target: (
            calls.append((received_service, received_target)) or outcome
        ),
    )
    assert action.obtain_owned_library_item(service, target) is outcome
    assert calls == [(service, target)]


def test_provider_failure_cannot_escape_with_secret_or_legacy_fallback(monkeypatch):
    from cifereca.core.selected_source_offer import SourceOfferTarget
    target = SourceOfferTarget(
        identity_id="alice", provider_id="future-store",
        credential_id="credential-A", account_id="account-A",
        item_id="edition-1", branch="selected", session=NS(),
    )
    secret = "Bearer PRIVATE token embedded in URL"
    def fail(*_args):
        raise RuntimeError(secret)
    monkeypatch.setattr(selection, "obtain_source_offer", fail)
    with pytest.raises(action.OwnedDownloadRejected, match="owned_download_unavailable") as exc:
        action.obtain_owned_library_item(NS(), target)
    assert secret not in str(exc.value)
    assert exc.value.__cause__ is None


def test_old_kobo_only_target_and_legacy_materializer_are_deleted():
    assert not hasattr(action, "OwnedDownloadTarget")
    assert not hasattr(action, "_resolve")
    assert not hasattr(action, "MaterializationCoordinator")
