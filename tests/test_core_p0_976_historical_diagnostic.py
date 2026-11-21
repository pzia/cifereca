"""Synthetic historical diagnostics; never a live provider or runtime."""
from dataclasses import replace

import pytest

from cifereca.core import adept_historical_diagnostic as diagnostic
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, target, publish, rows, BYTES,
)


def _setup(store, tmp_path):
    source, snapshot = new_source(kind="loan", branch="loans", right="loan-1")
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    result = publish(store, root, path, source, snapshot,
                     adept_receipt_ref="opaque-receipt")
    store.acquisition_put({
        "acquisition_id": "historical", "identity_id": "A", "provider_id": "seller",
        "credential_id": "card-A", "account_id": "buyer-A", "item_id": "edition",
        "status": "complete", "strategy": "loan_auto", "executor": "kobo-adept",
        "metadata": {"provider_loan_id": "loan-1", "adept_binding": {
            "adept_profile_id": "historical-adobe", "adept_loan_id": "adobe-loan",
        }},
    })
    return source, root, path, result


def _review(store, source, root, **kwargs):
    args = dict(inventory_profile_id="historical-adobe", inventory_complete=True,
                inventory_loans=[dict(id="adobe-loan", returned=False, expired=False,
                                      token_present=True, validity="2030-01-01")])
    args.update(kwargs)
    return diagnostic.review_historical_loan_diagnostic(
        store, source=source, library_root=root, **args,
    )


def test_three_observations_do_not_attest_a_join_or_grant(store, tmp_path):
    source, root, path, result = _setup(store, tmp_path)
    before = rows(store)
    ledger = store.acquisition_get("historical")
    report = _review(store, source, root, verify_local_bytes=True)
    assert report["inventory"]["inventory_comparison"] == "exact_profile_and_loan_reported"
    assert report["publication"]["canonical_origin_records"] == "provider_and_adept_recorded"
    assert report["local_bytes"]["selection"] == "unique_recorded_candidate"
    assert report["local_bytes"]["evidence"]["placement_evidence"] == "verified_artifact_bytes"
    for key in ("common_snapshot_attested", "inventory_attests_origin_link",
                "receipt_attests_exact_placement", "activation_attested",
                "may_rebind_or_return", "may_reacquire"):
        assert report[key] is False
    assert report["provider_loan_state"] == "not_checked"
    assert "opaque-receipt" not in repr(report) and result.artifact_id not in repr(report)
    assert rows(store) == before and path.read_bytes() == BYTES
    assert store.acquisition_get("historical") == ledger


def test_default_is_sql_only(store, tmp_path, monkeypatch):
    source, root, _, _ = _setup(store, tmp_path)
    monkeypatch.setattr(diagnostic, "_candidate", lambda *a: pytest.fail("not requested"))
    report = _review(store, source, root)
    assert report["local_bytes"] == {"selection": "not_requested", "evidence": None}


@pytest.mark.parametrize("change", ["missing", "changed", "symlink"])
def test_stale_record_is_not_physical_proof(store, tmp_path, change):
    source, root, path, _ = _setup(store, tmp_path)
    if change == "missing":
        path.unlink()
    elif change == "changed":
        path.write_bytes(b"unrelated")
    else:
        other = path.with_name("other.pdf")
        path.rename(other)
        path.symlink_to(other)
    report = _review(store, source, root, verify_local_bytes=True)
    assert report["publication"]["canonical_placement_state"] == "present_recorded"
    assert report["local_bytes"]["evidence"]["placement_evidence"] == "bytes_unverified"
    assert not report["may_reacquire"]


def test_profile_mismatch_does_not_rewrite_local_observation(store, tmp_path):
    source, root, _, _ = _setup(store, tmp_path)
    report = _review(store, source, root, inventory_profile_id="other", verify_local_bytes=True)
    assert report["inventory"]["inventory_comparison"] == "profile_mismatch"
    assert report["local_bytes"]["evidence"]["placement_evidence"] == "verified_artifact_bytes"
    assert not report["inventory_attests_origin_link"]


def test_two_present_placements_not_chosen_by_path(store, tmp_path, monkeypatch):
    source, root, path, result = _setup(store, tmp_path)
    other = path.with_name("other.pdf")
    other.write_bytes(BYTES)
    store.placement_put({
        "artifact_id": result.artifact_id, "identity_id": "A",
        "path": str(other), "state": "present", "metadata": {},
    })
    monkeypatch.setattr(diagnostic, "review_historical_adept_evidence",
                        lambda *a, **k: pytest.fail("ambiguous candidate"))
    report = _review(store, source, root, verify_local_bytes=True)
    assert report["local_bytes"] == {"selection": "ambiguous_present_placements", "evidence": None}


def test_changed_candidate_is_not_reported(store, tmp_path, monkeypatch):
    source, root, path, _ = _setup(store, tmp_path)
    real = diagnostic.review_historical_adept_evidence
    def changed(*args, **kwargs):
        evidence = real(*args, **kwargs)
        with store._lock, store._connect() as db:
            db.execute("UPDATE placements SET state='missing' WHERE path=?", (str(path),))
        return evidence
    monkeypatch.setattr(diagnostic, "review_historical_adept_evidence", changed)
    report = _review(store, source, root, verify_local_bytes=True)
    assert report["local_bytes"] == {"selection": "candidate_changed", "evidence": None}


def test_other_source_never_reads_file(store, tmp_path, monkeypatch):
    source, root, _, _ = _setup(store, tmp_path)
    other = replace(source, right_id="other")
    monkeypatch.setattr(diagnostic, "_candidate", lambda *a: pytest.fail("wrong source"))
    report = _review(store, other, root, verify_local_bytes=True)
    assert report["publication"]["source_reference_record"] == "not_recorded"
    assert report["local_bytes"]["selection"] == "canonical_candidate_unavailable"


@pytest.mark.parametrize("reader", ["inventory", "publication", "candidate", "physical", "combined"])
def test_readers_never_settle_a_caller_transaction(store, tmp_path, reader):
    from cifereca.core.adept_historical_inventory_join import review_legacy_adept_inventory
    from cifereca.core.adept_historical_publication_review import review_legacy_adept_publication
    from cifereca.core.adept_historical_evidence import review_historical_adept_evidence

    source, root, path, result = _setup(store, tmp_path)
    sql = store._connect()
    sql.execute("BEGIN IMMEDIATE")
    sql.execute("INSERT INTO metadata(key,value) VALUES('caller.historical.test','pending')")
    try:
        if reader == "inventory":
            verdict = review_legacy_adept_inventory(
                store, source=source, inventory_profile_id="historical-adobe",
                inventory_complete=True, inventory_loans=[])
            assert verdict["recorded_binding"] == "ledger_unverified"
        elif reader == "publication":
            verdict = review_legacy_adept_publication(store, source=source)
            assert verdict["source_reference_record"] == "storage_unverified"
        elif reader == "candidate":
            with pytest.raises(ValueError, match="historical_adept_nested_transaction"):
                diagnostic._candidate(store, source)
        elif reader == "physical":
            verdict = review_historical_adept_evidence(
                store, source=source, library_root=root, placement_id=result.placement_id,
                adept_receipt_ref="opaque-receipt")
            assert verdict["adept_origin"] == "unverified"
            assert verdict["placement_evidence"] == "not_checked"
        else:
            verdict = _review(store, source, root, verify_local_bytes=True)
            assert verdict["inventory"]["recorded_binding"] == "ledger_unverified"
            assert verdict["publication"]["source_reference_record"] == "storage_unverified"
            assert verdict["local_bytes"]["evidence"] is None and not verdict["may_reacquire"]
        assert sql.in_transaction
        assert sql.execute("SELECT value FROM metadata WHERE key='caller.historical.test'").fetchone() == ("pending",)
        assert path.read_bytes() == BYTES
    finally:
        if sql.in_transaction:
            sql.rollback()
    assert store.metadata_get("caller.historical.test") is None
    assert _review(store, source, root, verify_local_bytes=True)["local_bytes"]["evidence"]["placement_evidence"] == "verified_artifact_bytes"
