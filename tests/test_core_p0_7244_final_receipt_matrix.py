"""#7244: source/offer matrix for exact, rotated and ambiguous final receipts.

All records are canonical completed-delivery checkpoints on disposable
SQLite/FS. Observing a receipt never authorizes replay, unlocks a v1 hold,
or calls the supplier.
"""
import pytest

from cifereca.core import adept_historical_maintenance as review
from cifereca.core import canonical_adept_attempt as marker
from cifereca.core import canonical_recovery as recovery
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.locking import InterProcessLock
from tests.test_core_p0_976_historical_maintenance import _setup, store
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, rows, target, register,
)


@pytest.mark.parametrize("offers,expected", [
    (("held-offer", "rotated-offer"), "verified_final_receipt_observed"),
    (("rotated-offer",), "verified_other_offer_receipt_observed"),
    (("rotated-offer", "another-offer"), "unverified"),
])
def test_final_receipt_offer_rotation_is_precise_and_read_only(
        store, tmp_path, monkeypatch, offers, expected):
    service, _, original, _ = _setup(store, tmp_path)
    source, snapshot = new_source(kind="loan", branch="loans", right="pending")
    persist_attested_source_references(store, snapshot)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    final = target(tmp_path, "A")
    for offer in offers:
        with InterProcessLock(lock_path) as lock:
            recovery.record_completed_delivery_for_recovery(
                store, source=source, offer_id=offer,
                completed=CompletedDelivery(
                    source, offer, final, SHA, "receipt",
                    adept_receipt_ref="isolated-adept-ref"),
                library_root=tmp_path / "EBooks", shared_lock=lock)
    hold = marker._value("held-offer", "a" * 32)
    store.metadata_set(marker._key(source), hold)
    service.execute_acquisition_request = lambda *_a, **_k: pytest.fail(
        "final receipt review must not acquire")
    baseline = rows(store), original.read_bytes(), final.read_bytes(), lock_path.read_bytes()
    request = review.admit_historical_review(service)
    assert request is not None
    page = review.run_historical_hold_review(
        service, request, verify_local_bytes=True)
    index = next(i for i, row in enumerate(page["rows"])
                 if row["provider_loan_id"] == source.right_id)
    selection = review.select_historical_hold(
        service, request, page, ordinal=index, confirmed=True)
    assert selection is not None and selection.current()
    assert selection.final_receipt_evidence == expected
    assert selection.unlock_authorized is False
    if expected == "verified_final_receipt_observed":
        assert selection.rearm_blocker == "final_receipt_precedence"
    elif expected == "verified_other_offer_receipt_observed":
        assert selection.rearm_blocker == "other_offer_receipt_precedence"
    else:
        # No valid uniquely selected completed receipt can prove absence or
        # authorize replay; the v1 hold's phase remains ambiguous.
        assert selection.rearm_blocker == "historical_phase_ambiguous"
    assert (rows(store), original.read_bytes(), final.read_bytes(),
            lock_path.read_bytes()) == baseline
    assert store.metadata_get(marker._key(source)) == hold


def test_other_account_checkpoint_cannot_satisfy_this_loan_hold(
        store, tmp_path, monkeypatch):
    service, _, original, _ = _setup(store, tmp_path)
    source, snapshot = new_source(kind="loan", branch="loans", right="pending")
    persist_attested_source_references(store, snapshot)
    register(store, "A", "seller", "card-A", account="another-account")
    foreign, foreign_snapshot = new_source(
        kind="loan", branch="loans", right="pending", account="another-account")
    persist_attested_source_references(store, foreign_snapshot)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    final = target(tmp_path, "A")
    with InterProcessLock(lock_path) as lock:
        recovery.record_completed_delivery_for_recovery(
            store, source=foreign, offer_id="held-offer",
            completed=CompletedDelivery(
                foreign, "held-offer", final, SHA, "receipt",
                adept_receipt_ref="foreign-account-ref"),
            library_root=tmp_path / "EBooks", shared_lock=lock)
    hold = marker._value("held-offer", "a" * 32)
    store.metadata_set(marker._key(source), hold)
    baseline = rows(store), original.read_bytes(), final.read_bytes(), lock_path.read_bytes()
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request, verify_local_bytes=True)
    index = next(i for i, row in enumerate(page["rows"])
                 if row["provider_loan_id"] == source.right_id
                 and row["account_id"] == source.scope.account_id)
    selected = review.select_historical_hold(
        service, request, page, ordinal=index, confirmed=True)
    assert selected is not None and selected.final_receipt_evidence == "not_observed"
    assert selected.unlock_authorized is False
    assert (rows(store), original.read_bytes(), final.read_bytes(),
            lock_path.read_bytes()) == baseline
    assert store.metadata_get(marker._key(source)) == hold
