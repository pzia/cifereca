"""#3171/#3719: post-B stock repair failure cannot erase verified receipt.

One exact provider OriginLink and physically hashed Placement already exist.
Stock repair is optional; only re-verification of that same source can decide
whether an exception really invalidated B. All SQLite/files are synthetic.
"""
from dataclasses import replace

import pytest

from cifereca.core import convergence_execution
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, publish, target
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


@pytest.mark.parametrize("failure", ["missing_receipt", "io"])
def test_stock_repair_failure_keeps_verified_published_origin(rig, monkeypatch, failure):
    service, db, root = rig
    source = service.source
    service.snapshot = replace(service.snapshot, rights=(
        replace(
            service.snapshot.rights[0], delivery="available",
            delivery_offer_id="stable-offer",
        ),
    ))
    physical = target(root.parent, "A")
    pub = publish(db, root, physical, source, service.snapshot)
    before_refs = refs(db)
    service._canonical_persisted_consent = lambda _: frozenset({source})
    service._canonical_right_delivery = lambda *_: pytest.fail(
        "verified B must never trigger another provider GET"
    )
    calls = []

    def broken_repair(*_args, **_kwargs):
        calls.append("repair")
        if failure == "io":
            raise OSError("synthetic missing post-B staging")
        raise ValueError("canonical_stock_receipt_missing_or_ambiguous")

    monkeypatch.setattr(
        convergence_execution, "repair_exact_published_staging",
        broken_repair,
    )
    summary = synchronize_account_source(service, service.account)
    assert calls == ["repair"]
    assert summary["canonical_execution"]["present_attested"] == 1
    assert summary["canonical_execution"]["failed"] == 0
    assert summary["canonical_execution"]["downloaded"] == 0
    assert refs(db) == before_refs
    assert db.placement_get(pub.placement_id)["state"] == "present"
    assert physical.read_bytes() == BYTES


def test_stock_repair_failure_with_changed_physical_bytes_remains_failed(
    rig, monkeypatch,
):
    service, db, root = rig
    source = service.source
    service.snapshot = replace(service.snapshot, rights=(
        replace(
            service.snapshot.rights[0], delivery="available",
            delivery_offer_id="stable-offer",
        ),
    ))
    physical = target(root.parent, "A")
    pub = publish(db, root, physical, source, service.snapshot)
    before_refs = refs(db)
    service._canonical_persisted_consent = lambda _: frozenset({source})
    service._canonical_right_delivery = lambda *_: pytest.fail(
        "do not retry GET during an uncertain post-B repair"
    )

    def corrupt_during_repair(*_args, **_kwargs):
        physical.write_bytes(b"synthetic changed content")
        raise ValueError("canonical_stock_bytes_changed")

    monkeypatch.setattr(
        convergence_execution, "repair_exact_published_staging",
        corrupt_during_repair,
    )
    summary = synchronize_account_source(service, service.account)
    assert summary["canonical_execution"]["failed"] == 1
    assert summary["canonical_execution"]["present_attested"] == 0
    assert summary["canonical_execution"]["downloaded"] == 0
    assert refs(db) == before_refs
    assert db.placement_get(pub.placement_id)["state"] == "present"
    assert physical.read_bytes() == b"synthetic changed content"


def test_unrepairable_staging_cannot_downgrade_another_verified_final_copy(
    rig, monkeypatch,
):
    """A stale staging Placement is not a veto over a separately finalised B.

    Same account, SourceReference, Artifact and bytes: one final copy is
    verified even though another legacy staging copy has no matching receipt.
    The Sync must not retry the supplier GET or discard either copy.
    """
    service, db, root = rig
    # The real staging guard must check the same synthetic acquisition lock
    # as the Core Sync; otherwise the fixture would reject prematurely.
    monkeypatch.setattr(
        "cifereca.core.canonical_staging_stock.acquisition_lock_file",
        lambda: root.parent / "state" / "acquisition.lock",
    )
    source = service.source
    service.snapshot = replace(service.snapshot, rights=(
        replace(
            service.snapshot.rights[0], delivery="available",
            delivery_offer_id="stable-offer",
        ),
    ))
    final = target(root.parent, "A")
    published = publish(db, root, final, source, service.snapshot)

    # A historical staging-looking placement shares the same exact provider
    # OriginLink/Artifact but has no receipt proving its own promotion.
    staged = (root / "A" / ".canonical-delivery"
              / ("run-" + "a" * 32) / "received.pdf")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(BYTES)
    staged_id = db.placement_put({
        "artifact_id": published.artifact_id,
        "path": str(staged),
        "identity_id": source.scope.identity_id,
        "state": "present",
        "metadata": {},
    })
    before_refs = refs(db)
    service._canonical_persisted_consent = lambda _: frozenset({source})
    service._canonical_right_delivery = lambda *_: pytest.fail(
        "one final verified provider copy must prevent a second GET"
    )

    # The real stock repair rejects the staged copy without its exact
    # durable receipt; the other final Placement remains independently valid.
    result = synchronize_account_source(service, service.account)
    execution = result["canonical_execution"]
    assert execution["present_attested"] == 1
    assert execution["failed"] == 0
    assert execution["downloaded"] == 0
    assert refs(db) == before_refs
    assert db.placement_get(published.placement_id)["path"] == str(final)
    assert db.placement_get(staged_id)["path"] == str(staged)
    assert final.read_bytes() == staged.read_bytes() == BYTES
