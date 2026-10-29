"""#3171/#3252: Core selected-offer admission; real SQLite, synthetic sessions.

No provider network or DRM. A selected positive preflight cannot make a file,
authorize itself, or publish an origin without independent completed delivery.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


@pytest.fixture(autouse=True)
def _selected_preflight_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_offers.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )


def _enabled(service, root):
    consent = {service.source}
    service._canonical_persisted_consent = lambda _account: frozenset(consent)
    downloads = []

    def deliver(key, right):
        downloads.append((key, right.delivery_offer_id))
        path = root / "A" / "selected-delivered.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, right.delivery_offer_id, path, SHA,
            "receipt-of-exact-selected-completed-delivery",
        )

    service._canonical_right_delivery = deliver
    return consent, downloads


def _selected(service, *, offer="exact-selected-offer"):
    right = replace(service.snapshot.rights[0],
                    delivery="available", delivery_offer_id=offer)
    return replace(service.snapshot, rights=(right,),
                   status="partial", coverage="partial")


def test_selected_offer_admitted_only_once_and_second_sync_skips_private_get(rig):
    service, db, root = rig
    consent, downloads = _enabled(service, root)
    preflights = []

    def selected(account, **kwargs):
        preflights.append(kwargs)
        return _selected(service)

    service.session.rights_selected_delivery_offer_snapshot = selected
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert first["canonical_execution"]["pending"] == 0
    assert len(downloads) == 1
    assert len(preflights) == 1
    assert preflights[0] == {
        "edition_id": service.source.edition_id,
        "right_id": service.source.right_id, "kind": service.source.kind,
        "branch": service.source.branch, "explicit_user_action": True,
    }
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 1
    assert db.placements()[0]["identity_id"] == "A"

    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert len(preflights) == len(downloads) == 1
    assert refs(db)[1] == 1


def test_selected_preflight_is_never_called_without_persisted_consent(rig):
    service, db, root = rig
    service._canonical_persisted_consent = lambda _: frozenset()
    service._canonical_right_delivery = lambda *_: pytest.fail("no authorized GET")
    service.session.rights_selected_delivery_offer_snapshot = (
        lambda *_a, **_kw: pytest.fail("preflight without consent")
    )
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["pending"] == 1
    assert refs(db)[1] == 0 and db.placements() == []


def test_revocation_during_private_preflight_never_reaches_delivery(rig):
    service, db, root = rig
    consent, downloads = _enabled(service, root)

    def selected(*_args, **_kwargs):
        consent.clear()
        return _selected(service)

    service.session.rights_selected_delivery_offer_snapshot = selected
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["pending"] == 1
    assert downloads == [] and db.placements() == [] and refs(db)[1] == 0


@pytest.mark.parametrize("spoof", ("scope", "branch", "edition", "empty_offer"))
def test_foreign_or_unoffered_selection_cannot_enter_executor(rig, spoof):
    service, db, root = rig
    _, downloads = _enabled(service, root)

    def selected(*_args, **_kwargs):
        reply = _selected(service)
        if spoof == "scope":
            return replace(reply, scope=ProviderRightsScope(
                "B", "seller-a", "card-a", "buyer-a"))
        if spoof == "branch":
            return replace(reply, branch="foreign")
        if spoof == "edition":
            return replace(reply, rights=(replace(
                reply.rights[0], edition_id="other-edition"),))
        return replace(reply, rights=(replace(
            reply.rights[0], delivery="unknown", delivery_offer_id=""),))

    service.session.rights_selected_delivery_offer_snapshot = selected
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["pending"] == 1
    assert downloads == [] and db.placements() == [] and refs(db)[1] == 0


@pytest.mark.parametrize("kind", ["foreign", "duplicate", "unavailable"])
def test_upstream_session_borrower_rebound_denies_private_offer_read(rig, kind):
    service, db, root = rig
    _, downloads = _enabled(service, root)
    if kind == "foreign":
        service.session.accounts = [replace(service.account, unique_id="other-borrower")]
    elif kind == "duplicate":
        service.session.accounts = [service.account, replace(service.account)]
    else:
        service.session.accounts = None
    service.session.rights_selected_delivery_offer_snapshot = (
        lambda *_a, **_kw: pytest.fail("foreign session must not read selected offer")
    )
    # The new A reader refuses the stale borrower BEFORE selected-offer
    # preflight, so there is no admission lease and no preflight exception.
    result = synchronize_account_source(service, service.account)
    assert result["canonical_rights"]["status"] == "failed"
    assert "canonical_execution" not in result
    assert downloads == [] and db.placements() == [] and refs(db) == ([], 0)


def test_upstream_borrower_change_during_selected_offer_read_blocks_get(rig):
    service, db, root = rig
    _, downloads = _enabled(service, root)

    def selected(*_args, **_kwargs):
        service.session.accounts = [replace(service.account, unique_id="switched")]
        return _selected(service)

    service.session.rights_selected_delivery_offer_snapshot = selected
    with pytest.raises(ValueError, match="canonical_offer_source_rebound"):
        synchronize_account_source(service, service.account)
    assert downloads == [] and db.placements() == [] and refs(db)[1] == 0


def test_changed_epoch_during_offer_preflight_refuses_entire_old_session(rig):
    service, db, root = rig
    _, downloads = _enabled(service, root)

    def selected(*_args, **_kwargs):
        service._account_context_epoch += 1
        return _selected(service)

    service.session.rights_selected_delivery_offer_snapshot = selected
    with pytest.raises(ValueError, match="canonical_offer_source_rebound"):
        synchronize_account_source(service, service.account)
    assert downloads == [] and db.placements() == [] and refs(db)[1] == 0


def test_only_consented_source_from_multiple_rights_may_preflight(rig):
    service, db, root = rig
    _, downloads = _enabled(service, root)
    other = replace(service.snapshot.rights[0],
                    edition_id="other-edition", right_id="other-right")
    service.snapshot = replace(
        service.snapshot, rights=service.snapshot.rights + (other,))
    selected_calls = []

    def selected(_account, **kwargs):
        selected_calls.append(kwargs["right_id"])
        return _selected(service)

    service.session.rights_selected_delivery_offer_snapshot = selected
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert result["canonical_execution"]["pending"] == 1
    assert selected_calls == [service.source.right_id]
    assert len(downloads) == 1 and refs(db)[1] == 1


@pytest.mark.parametrize("selected_only", [False, True])
def test_archived_delivery_is_non_error_without_content_get(rig, selected_only):
    service, db, root = rig
    _, downloads = _enabled(service, root)
    archived = replace(service.snapshot.rights[0], delivery="unavailable",
                       delivery_offer_id="", delivery_unavailable_reason="archived")
    selected_calls = []
    if selected_only:
        def selected(*_args, **_kw):
            selected_calls.append(1)
            return replace(service.snapshot, rights=(archived,), status="partial", coverage="partial")
        service.session.rights_selected_delivery_offer_snapshot = selected
    else:
        service.snapshot = replace(service.snapshot, rights=(archived,))
        service.session.rights_selected_delivery_offer_snapshot = (
            lambda *_a, **_kw: pytest.fail("known archive needs no selected offer read"))
    for _ in range(2):
        result = synchronize_account_source(service, service.account)
        counts = result["canonical_execution"]
        assert counts["skipped_archived"] == 1
        assert counts["pending"] == counts["blocked"] == counts["failed"] == 0
        assert counts["downloaded"] == 0
        assert downloads == [] and db.placements() == [] and refs(db)[1] == 0
        assert len(refs(db)[0]) == 1  # Positive right retained, not a receipt.
    assert len(selected_calls) == (2 if selected_only else 0)


def test_generic_unavailable_is_not_retried_or_misclassified_as_archived(rig):
    service, db, root = rig
    _, downloads = _enabled(service, root)
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="unavailable", delivery_offer_id=""),))
    service.session.rights_selected_delivery_offer_snapshot = (
        lambda *_a, **_kw: pytest.fail("unavailable delivery needs no selected read"))
    result = synchronize_account_source(service, service.account)
    counts = result["canonical_execution"]
    assert counts["pending"] == 1 and counts["skipped_archived"] == 0
    assert counts["failed"] == 0 and downloads == []
