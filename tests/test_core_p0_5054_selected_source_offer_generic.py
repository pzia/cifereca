"""#5054 — synthetic provider, current selected offer, no persisted rights."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.plugin_api.downloadable_resources import (
    DownloadCapability, ResourceObservation, ResourceSnapshot, SourceReference,
)
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.core.selected_source_offer import (
    SourceOfferTarget, select_source_offer_target, obtain_source_offer,
)
import cifereca.core.selected_source_offer as selection
import cifereca.core.explicit_owned_canonical as canonical


class FakeSession:
    selected_purchase_branch = "private-purchases"
    def downloadable_selected_delivery_candidate_guarded(self, **kwargs):
        raise AssertionError("Only the Core executor may GET a candidate")
    def downloadable_selected_offer_snapshot(self, account, **kwargs):
        return self.observed


def _rig(tmp_path):
    account = Account(
        identity_id="reader", provider_id="fictional-books",
        credential_id="credential-A", unique_id="account-A",
    )
    session = FakeSession()
    session.provider_id = account.provider_id
    session.credential_id = account.credential_id
    session.accounts = [account]
    session.connected = True
    session._closed = False
    scope = ProviderRightsScope("reader", "fictional-books", "credential-A", "account-A")
    observed = ResourceSnapshot(
        scope=scope, branch="private-purchases", status="success",
        coverage="partial",
        resources=(ResourceObservation(
            source=SourceReference(
                scope, "private-purchases", "edition-1", "edition-1", "purchase",
            ),
            capability=DownloadCapability("available", "current-offer"),
            evidence_ref="current-observation", observed_at="2026-11-11T06:00:00Z",
        ),),
    )
    session.observed = observed
    file = tmp_path / "book.epub"
    file.write_bytes(b"test-only-staged-by-core")
    storage = NS(placements=lambda: [{
        "identity_id": "reader", "provider_id": "fictional-books",
        "artifact_id": "sha256:fixture", "state": "present",
        "source_item_id": "edition-1", "placement_id": "placement-A",
    }])
    service = NS(
        accounts=[account],
        _sessions={("fictional-books", "credential-A"): session},
        _session_key=lambda *coords: coords,
        current_identity=NS(identity_id="reader"),
        _source_context_epoch=1,
        storage=storage,
        library_dir=lambda: tmp_path,
        local_book_path=lambda *_a, **_kw: file,
        sync_source_enabled=lambda _a: True,
        identity_by_id=lambda identity: NS(identity_id=identity),
    )
    return service, account, session, observed, file


def test_passive_target_scopes_one_exact_account_and_no_supplier_get(monkeypatch, tmp_path):
    service, account, session, _, _ = _rig(tmp_path)
    seen = []
    monkeypatch.setattr(
        selection, "exact_private_inventory_binding_valid",
        lambda _service, _account, *, session, provider_id:
            seen.append((_account, session, provider_id)) or True,
    )
    monkeypatch.setattr(selection, "has_complete_delivery_abi", lambda _session: True)
    target = select_source_offer_target(
        service, provider_id="fictional-books",
        credential_id="credential-A", item_id="edition-1", identity_id="reader",
    )
    assert type(target) is SourceOfferTarget
    assert target.entitlement_id is None
    assert target.branch == "private-purchases"
    assert seen and all(row[0] is account and row[1] is session for row in seen)
    assert select_source_offer_target(
        service, provider_id="fictional-books",
        credential_id="foreign", item_id="edition-1", identity_id="reader",
    ) is None
    service.accounts.append(Account(
        identity_id="reader", provider_id="fictional-books",
        credential_id="credential-A", unique_id="account-B",
    ))
    assert select_source_offer_target(
        service, provider_id="fictional-books",
        credential_id="credential-A", item_id="edition-1", identity_id="reader",
    ) is None


def test_exact_current_offer_uses_generic_publisher_without_entitlement(monkeypatch, tmp_path):
    service, account, session, observed, path = _rig(tmp_path)
    target = SourceOfferTarget(
        "reader", "fictional-books", "credential-A", "account-A",
        "edition-1", "private-purchases", session,
    )
    calls = []
    original = session.downloadable_selected_offer_snapshot
    def select(account, **kw):
        calls.append(("offer", kw))
        return original(account, **kw)
    session.downloadable_selected_offer_snapshot = select

    class Lock:
        def __init__(self, *_a): pass
        def __enter__(self): return self
        def __exit__(self, *_a): pass

    monkeypatch.setattr(canonical, "InterProcessLock", Lock)
    monkeypatch.setattr(canonical, "acquisition_lock_file", lambda: tmp_path / "lock")
    monkeypatch.setattr(canonical, "has_complete_delivery_abi", lambda _: True)
    monkeypatch.setattr(canonical, "persist_attested_source_references",
                        lambda *_a, **_kw: calls.append(("references", None)))

    def execute(_service, _account, _session, snapshot, **kw):
        calls.append(("execute", None))
        assert kw["explicit_entitlement_id"] is None
        assert kw["explicit_offer_id"] == "current-offer"
        assert kw["explicitly_confirmed_once"] is True
        assert kw.get("expected_source_policy_revision") is None
        assert len(kw["persisted_consent"]) == 1
        key, = tuple(kw["persisted_consent"])
        assert kw["publication_guard"](None, key) is True
        assert kw["consent_still_current"](key) is True
        return [NS(source=key, status="downloaded", artifact_id="sha256:fixture")]

    monkeypatch.setattr(canonical, "execute_admitted_convergence", execute)
    result = canonical.obtain_explicit_owned(
        service, target,
        resolve=lambda _service, entitlement_id, identity, edition: (
            (target, None) if (entitlement_id, identity, edition) ==
            (None, "reader", "edition-1") else pytest.fail("target rebound")
        ),
        right_current=lambda *_args, **_kw: True,
    )
    assert result.artifact_id == "sha256:fixture"
    assert [row[0] for row in calls] == ["offer", "references", "execute"]
    assert calls[0][1]["explicit_user_action"] is True


def test_missing_current_offer_cannot_dispatch_convergence(monkeypatch, tmp_path):
    service, _, session, observed, _ = _rig(tmp_path)
    target = SourceOfferTarget(
        "reader", "fictional-books", "credential-A", "account-A",
        "edition-1", "private-purchases", session,
    )
    session.observed = replace(observed, resources=(
        replace(observed.resources[0],
                capability=DownloadCapability("unavailable", "")),
    ))
    class Lock:
        def __init__(self, *_a): pass
        def __enter__(self): return self
        def __exit__(self, *_a): pass
    monkeypatch.setattr(canonical, "InterProcessLock", Lock)
    monkeypatch.setattr(canonical, "acquisition_lock_file", lambda: tmp_path / "lock")
    monkeypatch.setattr(canonical, "has_complete_delivery_abi", lambda _: True)
    monkeypatch.setattr(
        canonical, "execute_admitted_convergence",
        lambda *_a, **_kw: pytest.fail("unavailable offer cannot start GET"),
    )
    with pytest.raises(ValueError, match="explicit_canonical_offer_unattested"):
        canonical.obtain_explicit_owned(
            service, target,
            resolve=lambda *_a: (target, None),
            right_current=lambda *_a, **_kw: True,
        )
