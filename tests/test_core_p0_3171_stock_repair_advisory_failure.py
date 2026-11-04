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
