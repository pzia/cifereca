"""#976 — checkpointed preview is advisory and rejects stale local evidence."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cifereca import cli
from cifereca.core import adept_resolution_checkpoint as checkpoint
from cifereca.core.adept_origin_audit import _opaque_ref
from cifereca.core.adept_resolution_snapshot import snapshot_paris_adept_local_evidence
from cifereca.storage import Storage
from tests.test_core_p1_976_manual_resolution_preview import _setup


REF = _opaque_ref("acquisition", "acq-paris-a")


def _review(store, *, loan="paris-loan", item="item-1",
            source="source-a", acquisition=REF):
    return checkpoint.preview_paris_adept_with_local_checkpoint(
        store,
        loan_id=loan, item_id=item, source_reference_id=source,
        acquisition_ref=acquisition, artifact_inspector=lambda _: None,
    )


def _assert_never_grants(result):
    assert result["return_authorized"] is False
    assert result["commit_available"] is False
    assert result["requires_explicit_review"] is True
    assert result["adobe_profile_resolution"] == "unknown"


def test_explicit_candidate_has_non_authorizing_stable_local_fingerprint(tmp_path):
    db, store = _setup(tmp_path)
    try:
        original_rights, original_receipts = deepcopy((store.rights, store.ledger))
        first = _review(store)
        second = _review(store)
        assert first == second
        assert first["status"] == "review_candidate"
        assert first["review_reasons"] == ["server_transaction_attestation_required"]
        assert first["local_evidence_status"] == "observed_unchanged_during_preview"
        assert first["local_evidence_algorithm"] == "sha256:paris-adept-local-evidence-v1"
        assert len(first["local_evidence_fingerprint"]) == 64
        assert first["recorded_adept_profile_id"] == "adobe-a"
        _assert_never_grants(first)
        assert "SECRET-" not in repr(first)
        assert store.rights == original_rights
        assert store.ledger == original_receipts
        assert store.calls == ["rights", "ledger"] * 6
        observed = snapshot_paris_adept_local_evidence(
            store, loan_id="paris-loan", item_id="item-1",
        )
        assert observed["status"] == "local_snapshot"
        assert observed["fingerprint"] == first["local_evidence_fingerprint"]
        _assert_never_grants(observed)
    finally:
        db.close()


def test_source_mismatch_keeps_refusal_without_a_commit_path(tmp_path):
    db, store = _setup(tmp_path)
    try:
        report = _review(store, source="not-in-database")
        assert report["status"] == "selection_missing"
        assert report["local_evidence_status"] == "observed_unchanged_during_preview"
        assert len(report["local_evidence_fingerprint"]) == 64
        _assert_never_grants(report)
    finally:
        db.close()


def test_local_receipt_changed_mid_preview_discards_candidate_and_profile(
    tmp_path, monkeypatch,
):
    db, store = _setup(tmp_path)
    original = checkpoint.preview_paris_adept_resolution_candidate

    def mutate_old_receipt(storage, **kwargs):
        result = original(storage, **kwargs)
        store.ledger[0]["metadata"]["adept_binding"]["adept_loan_id"] = (
            "DIFFERENT-PRIVATE-ADOBE-LOAN"
        )
        return result

    monkeypatch.setattr(
        checkpoint, "preview_paris_adept_resolution_candidate",
        mutate_old_receipt,
    )
    try:
        report = _review(store)
        assert report["status"] == "local_evidence_changed_during_preview"
        assert report["review_reasons"] == [
            "rerun_preview_after_local_evidence_change"
        ]
        assert report["local_evidence_fingerprint"] is None
        assert report["recorded_adept_profile_id"] is None
        assert report["recorded_adept_loan_ref"] is None
        assert report["acquisition_ref"] is None
        assert report["source_reference_id"] is None
        assert "DIFFERENT-PRIVATE" not in repr(report)
        _assert_never_grants(report)
    finally:
        db.close()


def test_snapshot_unavailable_refuses_to_expose_preview_candidate(
    tmp_path, monkeypatch,
):
    db = Storage(tmp_path / "no-canonical-evidence.db")
    try:
        from tests.test_core_p1_976_legacy_canonical_bridge import EvidenceStore
        store = EvidenceStore(db)
        monkeypatch.setattr(
            checkpoint, "preview_paris_adept_resolution_candidate",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("without local evidence preview must not execute")
            ),
        )
        report = _review(store)
        assert report["status"] == "local_evidence_unavailable"
        assert report["recorded_adept_profile_id"] is None
        assert report["local_evidence_fingerprint"] is None
        _assert_never_grants(report)
    finally:
        db.close()


@pytest.mark.parametrize(("loan", "item", "source", "ref"), [
    (" ", "item-1", "source-a", REF),
    ("paris-loan", "item-1", "", REF),
    ("paris-loan", "item-1", "source-a", ["PRIVATE"]),
    ("paris-loan", "item-1", "source-a", "not-sha"),
    ("paris-loan", "item-1", "source-a", REF.upper()),
])
def test_invalid_selection_fails_before_any_storage_access(
    loan, item, source, ref,
):
    class NoDb:
        def __getattribute__(self, _):
            raise AssertionError("bad selectors must not reach database")

    report = checkpoint.preview_paris_adept_with_local_checkpoint(
        NoDb(), loan_id=loan, item_id=item, source_reference_id=source,
        acquisition_ref=ref,
        artifact_inspector=lambda _: (_ for _ in ()).throw(
            AssertionError("bad selectors must not inspect files")
        ),
    )
    assert report["status"] == "invalid_selection"
    assert report["local_evidence_fingerprint"] is None
    assert "PRIVATE" not in repr(report)
    _assert_never_grants(report)
