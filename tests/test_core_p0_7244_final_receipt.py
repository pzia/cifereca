"""Actual isolated completed-receipt observation, never a replay permit."""
import json
import pytest
from cifereca.core import adept_historical_maintenance as review
from cifereca.core import canonical_adept_attempt as marker
from cifereca.core import canonical_recovery as recovery
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.locking import InterProcessLock
from tests.test_core_p0_976_historical_maintenance import _setup, store
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, target, SHA, BYTES


@pytest.mark.parametrize("case", ["exact", "other_offer", "other_offer_tampered", "tampered", "missing", "no_opt_in",
                                  "wrong_key", "wrong_body_source", "invalid_offer"])
def test_exact_final_receipt_observation_precedes_replay(store, tmp_path, monkeypatch, case):
    service, _, _, _ = _setup(store, tmp_path)
    source, snapshot = new_source(kind="loan", branch="loans", right="pending")
    persist_attested_source_references(store, snapshot)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda **_kw: lock_path)
    final = target(tmp_path, "A")
    offer = "private-offer"
    if case != "missing":
        staged_offer = "other-offer" if case in ("other_offer", "other_offer_tampered") else offer
        with InterProcessLock(lock_path) as lock:
            recovery.record_completed_delivery_for_recovery(
                store, source=source, offer_id=staged_offer,
                completed=CompletedDelivery(source, staged_offer, final, SHA, "receipt",
                                            adept_receipt_ref="private-adept-receipt"),
                library_root=tmp_path / "EBooks", shared_lock=lock)
    else:
        with InterProcessLock(lock_path):
            pass
    if case in ("wrong_key", "wrong_body_source", "invalid_offer"):
        checkpoint = recovery._stage_id(source, offer)
        with store._lock, store._connect() as sql:
            if case == "wrong_key":
                sql.execute("UPDATE acquisition_ledger SET acquisition_id=? WHERE acquisition_id=?",
                            ("corrupted-checkpoint-key", checkpoint))
            else:
                body, = sql.execute("SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
                                    (checkpoint,)).fetchone()
                data = json.loads(body)
                if case == "wrong_body_source":
                    data["source"] = {}
                else:
                    data["offer_id"] = None
                sql.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                            (json.dumps(data), checkpoint))
    raw = marker._value(offer, "a" * 32)
    store.metadata_set(marker._key(source), raw)
    if case in ("tampered", "other_offer_tampered"):
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
        "other_offer": "verified_other_offer_receipt_observed",
        "other_offer_tampered": "unverified", "tampered": "unverified",
        "missing": "not_observed", "no_opt_in": "not_checked",
        "wrong_key": "unverified", "wrong_body_source": "unverified", "invalid_offer": "unverified",
    }[case]
    assert store.metadata_get(marker._key(source)) == raw
    assert (final.read_bytes(), lock_path.read_bytes()) == before


@pytest.mark.parametrize("evidence, copy, inactive, phase, witness, expected", [
    ("verified_final_receipt_observed", "not_proven", True, "held_invalid", "none", "final_receipt_precedence"),
    ("verified_other_offer_receipt_observed", "not_proven", False, "held_phase_unknown", "non_submission_recorded", "other_offer_receipt_precedence"),
    ("not_observed", "verified_local_copy_observed", True, "held_invalid", "none", "local_copy_precedence"),
    ("not_observed", "not_proven", True, "held_phase_unknown", "non_submission_recorded", "recorded_loan_inactive"),
    ("not_observed", "not_proven", False, "held_invalid", "non_submission_recorded", "marker_unverified"),
    ("not_observed", "not_proven", False, "held_phase_unknown", "none", "historical_phase_ambiguous"),
    ("not_checked", "not_requested", False, "held_phase_unknown", "non_submission_recorded", "local_recovery_not_qualified"),
    ("unverified", "not_proven", False, "held_phase_unknown", "non_submission_recorded", "local_recovery_not_qualified"),
    ("not_observed", "not_proven", False, "held_phase_unknown", "non_submission_recorded", "historical_activation_unattested"),
])
def test_rearm_disposition_never_equates_non_submission_or_profile_with_permission(
        evidence, copy, inactive, phase, witness, expected):
    from types import SimpleNamespace
    from cifereca.gui.adept_historical_maintenance_line import _selected_hold_summary
    source, _ = new_source(kind="loan", branch="loans", right="private-loan")
    selected = review.HistoricalHoldSelection(
        request=None, source=source,
        marker=SimpleNamespace(state=phase),
        witness=SimpleNamespace(state=witness),
        stamp=(), copy_evidence=copy, final_receipt_evidence=evidence,
        recorded_inactive=inactive)
    assert selected.rearm_blocker == expected and selected.unlock_authorized is False
    assert "Retéléchargement non autorisé" in _selected_hold_summary(selected)
