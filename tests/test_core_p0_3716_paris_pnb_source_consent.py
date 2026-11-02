"""P0 #3716: Paris PNB loan source consent exercises the generic Core v2 path.

Synthetic v2 provider observations emulate an authenticated DILICOM account;
no Paris HTTP/SSO, real subscriber data or external GET is used.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import source_sync_consent
from cifereca.core.source_convergence import SourceKey
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.models import Account
from cifereca.plugin_api.downloadable_resources import OptionalRightMetadata
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, register
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3719_source_sync_consent import source


def _paris(source):
    """Rebind the provider-neutral fixture before any Core v2 observation."""
    service, db, root, calls, _ = source
    register(db, "A", "paris", "paris-card", account="reader-A")
    account = Account(
        identity_id="A", provider_id="paris",
        credential_id="paris-card", unique_id="reader-A",
    )
    scope = ProviderRightsScope("A", "paris", "paris-card", "reader-A")
    existing = service.resource_snapshot.resources[0]
    reference = replace(
        existing.source, scope=scope, branch="dilicom",
        edition_id="pnb-edition-1", resource_id="pnb-loan-1", kind="loan",
    )
    service.resource_snapshot = replace(
        service.resource_snapshot,
        scope=scope, branch="dilicom",
        resources=(replace(
            existing, source=reference,
            right=OptionalRightMetadata("unknown"),
        ),),
    )
    service.source = SourceKey(
        scope, reference.edition_id, reference.resource_id, "loan", "dilicom",
    )
    service.account = account
    service.accounts = [account]
    service.session.provider_id = "paris"
    service.session.credential_id = "paris-card"
    service.session.accounts = [account]
    service._sessions = {service._session_key("paris", "paris-card"): service.session}
    broker = SynchronizationBrokerImpl(service)
    return service, db, root, calls, broker


def _change(broker, account, enabled, revision=None):
    return broker.set_source_sync_consent(
        broker._source_id(account),
        enabled=enabled, expected_revision=revision,
    )


def test_paris_pnb_on_covers_unknown_and_missing_right_without_second_get(source):
    service, db, root, calls, broker = _paris(source)
    # Same authenticated DILICOM source, two distinct loan IDs. One right is
    # descriptive-unknown, the other has NO right record at all.
    first, = service.resource_snapshot.resources
    second = replace(first, source=replace(
        first.source, edition_id="pnb-edition-2",
        resource_id="pnb-loan-2",
    ), right=None)
    service.resource_snapshot = replace(
        service.resource_snapshot, resources=(first, second),
    )

    unset = broker.source_sync_consent(broker._source_id(service.account))
    assert unset["supported"] and not unset["configured"]
    off = _change(broker, service.account, False)
    denied = synchronize_account_source(service, service.account)
    assert denied["canonical_execution"]["downloaded"] == 0
    assert calls == [] and refs(db)[1] == 0

    on = _change(broker, service.account, True, off["revision"])
    assert on["enabled"] and on["revision"] != off["revision"]
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 2
    assert calls == ["offer", "offer", "delivery", "delivery"]
    assert db.entitlements() == []
    assert refs(db)[1] == 2
    placements = db.placements()
    assert len(placements) == 2
    assert all(p["identity_id"] == "A" for p in placements)
    assert all(Path(p["path"]).parent == root / "A" / "Acquisitions"
               and Path(p["path"]).read_bytes() == BYTES
               for p in placements)

    repeated = synchronize_account_source(service, service.account)
    assert repeated["canonical_execution"]["present_attested"] == 2
    assert repeated["canonical_execution"]["downloaded"] == 0
    assert calls == ["offer", "offer", "delivery", "delivery"]
    assert db.placements() == placements

    # Another Paris borrower on a different identity cannot inherit this ON.
    register(db, "B", "paris", "paris-card-B", account="reader-B")
    foreign = ProviderRightsScope("B", "paris", "paris-card-B", "reader-B")
    assert source_sync_consent.read_policy(db, foreign).mode == "unset"


def test_paris_pnb_revocation_during_candidate_does_not_publish(source):
    service, db, root, calls, broker = _paris(source)
    on = _change(broker, service.account, True)
    delivered = service.session.downloadable_selected_delivery_candidate

    def revoke_during_get(*args, **kwargs):
        candidate = delivered(*args, **kwargs)
        _change(broker, service.account, False, on["revision"])
        return candidate

    service.session.downloadable_selected_delivery_candidate = revoke_during_get
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert refs(db)[1] == 0
    assert db.placements() == []
    assert calls == ["offer", "delivery"]
    assert source_sync_consent.read_policy(
        db, source_sync_consent.scope_for_account(service.account)
    ).mode == "off"



def test_paris_pnb_failed_one_loan_does_not_block_next_or_repeat_valid_get(source):
    """An ADEPT-like per-loan failure must not starve other active PNB loans.

    This Core v2 fixture is fully offline; no real Adobe profile is touched.
    """
    service, db, root, calls, broker = _paris(source)
    first, = service.resource_snapshot.resources
    second = replace(first, source=replace(
        first.source, edition_id="pnb-edition-2",
        resource_id="pnb-loan-2",
    ), right=None)
    service.resource_snapshot = replace(
        service.resource_snapshot, resources=(first, second),
    )
    original = service.session.downloadable_selected_delivery_candidate

    def flaky(account, *, edition_id, **kwargs):
        if edition_id == "pnb-edition-1":
            raise ValueError("synthetic ADEPT receipt rejected")
        return original(account, edition_id=edition_id, **kwargs)

    service.session.downloadable_selected_delivery_candidate = flaky
    assert _change(broker, service.account, True)["enabled"] is True

    first_sync = synchronize_account_source(service, service.account)
    assert first_sync["status"] == "partial"
    assert first_sync["canonical_execution"]["failed"] == 1
    assert first_sync["canonical_execution"]["downloaded"] == 1
    assert db.entitlements() == []
    assert len(db.placements()) == 1
    path = Path(db.placements()[0]["path"])
    assert path.read_bytes() == BYTES and path.parent == root / "A" / "Acquisitions"
    assert calls == ["offer", "offer", "delivery"]

    previous_placements = db.placements()
    again = synchronize_account_source(service, service.account)
    assert again["canonical_execution"]["failed"] == 1
    assert again["canonical_execution"]["present_attested"] == 1
    assert again["canonical_execution"]["downloaded"] == 0
    assert db.placements() == previous_placements
    assert path.read_bytes() == BYTES
    assert calls == ["offer", "offer", "delivery", "offer"]
