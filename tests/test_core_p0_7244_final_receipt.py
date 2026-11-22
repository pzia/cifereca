"""Actual isolated completed-receipt observation, never a replay permit."""
import pytest
from cifereca.core import adept_historical_maintenance as review
from cifereca.core import canonical_adept_attempt as marker
from cifereca.core import canonical_recovery as recovery
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.locking import InterProcessLock
from tests.test_core_p0_976_historical_maintenance import _setup, store
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, target, SHA, BYTES


@pytest.mark.parametrize("case", ["exact", "other_offer", "tampered", "missing", "no_opt_in"])
def test_exact_final_receipt_observation_precedes_replay(store, tmp_path, monkeypatch, case):
    service, _, _, _ = _setup(store, tmp_path)
    source, snapshot = new_source(kind="loan", branch="loans", right="pending")
    persist_attested_source_references(store, snapshot)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    final = target(tmp_path, "A")
    offer = "private-offer"
    if case != "missing":
        staged_offer = "other-offer" if case == "other_offer" else offer
        with InterProcessLock(lock_path) as lock:
            recovery.record_completed_delivery_for_recovery(
                store, source=source, offer_id=staged_offer,
                completed=CompletedDelivery(source, staged_offer, final, SHA, "receipt",
                                            adept_receipt_ref="private-adept-receipt"),
                library_root=tmp_path / "EBooks", shared_lock=lock)
    else:
        with InterProcessLock(lock_path):
            pass
    raw = marker._value(offer, "a" * 32)
    store.metadata_set(marker._key(source), raw)
    if case == "tampered":
        final.write_bytes(b"changed")
    before = final.read_bytes(), lock_path.read_bytes()
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request,
                                            verify_local_bytes=case != "no_opt_in")
    index = next(i for i, row in enumerate(page["rows"])
                 if row["provider_loan_id"] == source.right_id)
    selected = review.select_historical_hold(service, request, page, ordinal=index, confirmed=True)
    assert selected is not None and not selected.unlock_authorized
    assert selected.final_receipt_evidence == {
        "exact": "verified_final_receipt_observed",
        "other_offer": "not_observed", "tampered": "unverified",
        "missing": "not_observed", "no_opt_in": "not_checked",
    }[case]
    assert store.metadata_get(marker._key(source)) == raw
    assert (final.read_bytes(), lock_path.read_bytes()) == before
