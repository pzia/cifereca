"""#976: three axes remain independent; local-only synthetic evidence."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core import adept_historical_evidence as audit
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, target, publish, rows, BYTES,
)


def _setup(store, tmp_path):
    source, snapshot = new_source(kind="loan", branch="loans", right="loan-1")
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    root = tmp_path / "EBooks"
    result = publish(store, root, path, source, snapshot,
                     adept_receipt_ref="opaque-adept-proof")
    return source, root, path, result


def _review(store, source, root, placement, receipt="opaque-adept-proof"):
    return audit.review_historical_adept_evidence(
        store, source=source, placement_id=placement, adept_receipt_ref=receipt,
        library_root=root,
    )


def test_exact_recorded_origin_and_bytes_do_not_prove_activation_or_right(store, tmp_path):
    source, root, path, result = _setup(store, tmp_path)
    before = rows(store)
    # Historical evidence survives connection disable; this is not consent.
    with store._lock, store._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0")
    report = _review(store, source, root, result.placement_id)
    assert report["adept_origin"] == "recorded_exact_source"
    assert report["provider_origin_recorded"] is True
    assert report["placement_evidence"] == "verified_artifact_bytes"
    assert report["activation_profile"] == "unknown"
    assert report["provider_loan_state"] == report["adept_license_state"] == "not_checked"
    assert not report["receipt_attests_exact_placement"]
    assert not report["may_rebind_or_return"] and not report["may_reacquire"]
    assert str(path) not in repr(report) and "opaque-adept-proof" not in repr(report)
    assert rows(store) == before and path.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["wrong_receipt", "other_loan", "other_account"])
def test_source_or_receipt_mismatch_cannot_borrow_origin(store, tmp_path, change):
    source, root, path, result = _setup(store, tmp_path)
    receipt = "opaque-adept-proof"
    if change == "wrong_receipt":
        receipt = "unrelated"
    elif change == "other_loan":
        source = replace(source, right_id="other")
    else:
        source = replace(source, scope=replace(source.scope, account_id="other"))
    report = _review(store, source, root, result.placement_id, receipt)
    assert report["adept_origin"] == "unverified"
    assert report["placement_evidence"] == "not_checked"
    assert not report["may_reacquire"]
    assert path.read_bytes() == BYTES


def test_same_sha_in_another_profile_not_local_placement_proof(store, tmp_path):
    source, root, _path, result = _setup(store, tmp_path)
    other = target(tmp_path, "B")
    register(store, "B", "other-seller", "card-B")
    other_id = store.placement_put({
        "artifact_id": result.artifact_id, "identity_id": "B",
        "path": str(other), "state": "present", "metadata": {},
    })
    report = _review(store, source, root, other_id)
    assert report["adept_origin"] == "recorded_exact_source"
    assert report["placement_evidence"] == "scope_unverified"
    assert other.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["missing", "symlink", "changed"])
def test_no_physical_proof_leaves_historical_origin_separate(store, tmp_path, change):
    source, root, path, result = _setup(store, tmp_path)
    if change == "missing":
        path.unlink()
    elif change == "symlink":
        other = path.with_name("other.pdf")
        path.rename(other)
        path.symlink_to(other)
    else:
        path.write_bytes(b"unrelated")
    before = rows(store)
    report = _review(store, source, root, result.placement_id)
    assert report["adept_origin"] == "recorded_exact_source"
    assert report["placement_evidence"] == "bytes_unverified"
    assert not report["receipt_attests_exact_placement"]
    assert rows(store) == before
    if change == "symlink":
        assert path.is_symlink()


def test_budget_stops_before_file_open(store, tmp_path, monkeypatch):
    source, root, _path, result = _setup(store, tmp_path)
    monkeypatch.setattr(audit, "_MAX_BYTES", 1)
    monkeypatch.setattr(audit, "_verified_variant_file",
                        lambda *a, **k: pytest.fail("must not open"))
    report = _review(store, source, root, result.placement_id)
    assert report["placement_evidence"] == "review_budget_exceeded"
