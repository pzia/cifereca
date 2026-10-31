"""#976 — human-selected ADEPT evidence is PREVIEW only, never a return grant."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from cifereca import cli

from cifereca.core.adept_origin_audit import _opaque_ref
from cifereca.core.adept_resolution_preview import (
    preview_paris_adept_resolution_candidate,
)
from cifereca.storage import Storage
from tests.test_core_p1_976_canonical_originlink_review import _artifact
from tests.test_core_p1_976_legacy_canonical_bridge import (
    EvidenceStore, _canonical, _historical_acq, _historical_right,
)


ACQUISITION_REF = _opaque_ref("acquisition", "acq-paris-a")


def _preview(store, *, source="source-a", acquisition=ACQUISITION_REF,
             loan="paris-loan", item="item-1"):
    return preview_paris_adept_resolution_candidate(
        store, loan_id=loan, item_id=item, source_reference_id=source,
        acquisition_ref=acquisition,
        artifact_inspector=lambda _: None,
    )


def _setup(tmp_path, *, canonical=True, paired=True, state="active",
           rights=None, acquisitions=None):
    storage = Storage(tmp_path / "preview.db")
    _artifact(storage)
    if canonical:
        with storage._lock, storage._connect() as db:
            _canonical(db, state=state, paired=paired)
    return storage, EvidenceStore(
        storage,
        rights=[_historical_right()] if rights is None else rights,
        acquisitions=[_historical_acq()] if acquisitions is None else acquisitions,
    )


def _no_permission(result):
    assert result["return_authorized"] is False
    assert result["commit_available"] is False
    assert result["requires_explicit_review"] is True
    assert result["adobe_profile_resolution"] == "unknown"


def test_single_locally_attested_candidate_is_still_only_a_manual_preview(tmp_path):
    db, store = _setup(tmp_path)
    try:
        old_rights = deepcopy(store.rights)
        old_ledger = deepcopy(store.ledger)
        first = _preview(store)
        second = _preview(store)
        assert first == second
        assert first["status"] == "review_candidate"
        assert first["review_reasons"] == ["server_transaction_attestation_required"]
        assert first["source_reference_id"] == "source-a"
        assert first["acquisition_ref"] == ACQUISITION_REF
        assert first["recorded_adept_profile_id"] == "adobe-a"
        assert first["recorded_adept_loan_ref"] is not None
        assert first["historical_evidence"] == "inventory_attested"
        assert "adept-lic" not in repr(first)
        _no_permission(first)
        assert store.rights == old_rights
        assert store.ledger == old_ledger
        assert store.calls == ["rights", "ledger", "rights", "ledger"]
    finally:
        db.close()


def test_other_library_destination_cannot_be_selected_as_same_source(tmp_path):
    db, store = _setup(
        tmp_path,
        acquisitions=[_historical_acq("bob", "paris-a")],
    )
    try:
        report = _preview(store)
        assert report["status"] == "source_scope_mismatch"
        assert report["review_reasons"] == [
            "acquisition_not_in_selected_library_scope",
        ]
        assert report["recorded_adept_profile_id"] == "adobe-a"
        _no_permission(report)
    finally:
        db.close()


def test_other_paris_borrower_cannot_be_assigned_to_selected_source(tmp_path):
    db, store = _setup(
        tmp_path,
        acquisitions=[_historical_acq("bob", "paris-b")],
    )
    try:
        report = _preview(store, acquisition=_opaque_ref("acquisition", "acq-paris-b"))
        assert report["status"] == "source_scope_mismatch"
        _no_permission(report)
    finally:
        db.close()


def test_historical_conflict_remains_blocking_even_after_manual_selection(tmp_path):
    original = _historical_acq()
    alternate = _historical_acq("bob", "paris-b", "adobe-conflicting")
    db, store = _setup(
        tmp_path, acquisitions=[original, alternate],
    )
    try:
        result = _preview(store)
        assert result["status"] == "conflicting_records"
        assert result["review_reasons"] == ["independent_origin_evidence_conflicts"]
        _no_permission(result)
    finally:
        db.close()


def test_duplicate_old_acquisition_ref_cannot_choose_an_arbitrary_record(tmp_path):
    first = _historical_acq()
    second = deepcopy(first)
    second["metadata"]["adept_binding"]["adept_profile_id"] = "second-adobe"
    db, store = _setup(tmp_path, acquisitions=[first, second])
    try:
        report = _preview(store)
        assert report["status"] == "ambiguous_acquisition"
        assert report["review_reasons"] == ["historical_acquisition_ref_reused"]
        assert report["acquisition_ref"] is None
        _no_permission(report)
    finally:
        db.close()


@pytest.mark.parametrize(("paired", "state", "rights", "strength", "expected"), [
    (False, "active", None, True, "canonical_links_unverified"),
    (True, "inactive", None, True, "source_unverified"),
    (True, "active", [], True, "source_unverified"),
    (True, "active", None, False, "historical_inventory_unverified"),
])
def test_incomplete_provenance_is_never_a_reviewable_attested_candidate(
    tmp_path, paired, state, rights, strength, expected,
):
    acq = _historical_acq(attested=strength)
    db, store = _setup(
        tmp_path, paired=paired, state=state, rights=rights,
        acquisitions=[acq],
    )
    try:
        report = _preview(store)
        assert report["status"] == expected
        assert report["recorded_adept_profile_id"] == "adobe-a"
        _no_permission(report)
    finally:
        db.close()


def test_selected_source_or_acquisition_must_exist_on_exact_transaction(tmp_path):
    db, store = _setup(tmp_path)
    try:
        assert _preview(store, source="other-source")["status"] == "selection_missing"
        assert _preview(store, acquisition="f" * 16)["status"] == "selection_missing"
        assert _preview(store, loan="different")["status"] == "selection_missing"
        assert _preview(store, item="other-item")["status"] == "selection_missing"
    finally:
        db.close()


@pytest.mark.parametrize(("loan", "item", "source", "acquisition"), [
    (" ", "item-1", "source-a", ACQUISITION_REF),
    ("paris-loan", "\x00item", "source-a", ACQUISITION_REF),
    ("paris-loan", "item-1", "", ACQUISITION_REF),
    ("paris-loan", "item-1", "source-a", "not-opaque"),
    ("paris-loan", "item-1", "source-a", "f" * 15),
    ("paris-loan", "item-1", "source-a", ["PRIVATE"]),
])
def test_invalid_selection_fails_without_storage_or_artifact_access(
    loan, item, source, acquisition,
):
    class Forbidden:
        def __getattribute__(self, attr):
            raise AssertionError("invalid selection may not touch persistent data")
    report = preview_paris_adept_resolution_candidate(
        Forbidden(),
        loan_id=loan, item_id=item, source_reference_id=source,
        acquisition_ref=acquisition,
        artifact_inspector=lambda _: (_ for _ in ()).throw(
            AssertionError("invalid selection must not inspect file")
        ),
    )
    assert report["status"] == "invalid_selection"
    assert report["review_reasons"] == ["invalid_exact_reference"]
    assert report["source_reference_id"] is None
    assert report["acquisition_ref"] is None
    _no_permission(report)
    assert "PRIVATE" not in repr(report)


def test_cli_preview_selection_is_offline_and_cannot_mutate_adobe(tmp_path, monkeypatch, capsys):
    db, store = _setup(tmp_path)
    try:
        core = SimpleNamespace(
            storage=store, close=lambda: None,
            library=SimpleNamespace(
                connect_configured=lambda: (_ for _ in ()).throw(
                    AssertionError("preview must not connect to providers")
                ),
            ),
        )
        monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_: core)
        assert cli.main([
            "paris-adept", "preview-resolution",
            "--loan-id", "paris-loan", "--item-id", "item-1",
            "--source-reference-id", "source-a",
            "--acquisition-ref", ACQUISITION_REF,
        ]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "review_candidate"
        assert report["recorded_adept_profile_id"] == "adobe-a"
        _no_permission(report)
        assert store.calls == ["rights", "ledger"]
    finally:
        db.close()


@pytest.mark.parametrize(("loan", "source", "acquisition"), [
    ("", "source-a", ACQUISITION_REF),
    (" paris-loan ", "source-a", ACQUISITION_REF),
    ("paris-loan", " source-a", ACQUISITION_REF),
    ("paris-loan", "source-a", "not-opaque"),
    ("paris-loan", "source-a", "f" * 15),
])
def test_cli_preview_rejects_invalid_selection_without_opening_core(
    monkeypatch, loan, source, acquisition,
):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("bad selection should not open Core")
        ),
    )
    with pytest.raises(SystemExit) as caught:
        cli.main([
            "paris-adept", "preview-resolution",
            "--loan-id", loan, "--item-id", "item-1",
            "--source-reference-id", source,
            "--acquisition-ref", acquisition,
        ])
    assert caught.value.code == 2


def test_two_canonical_branches_of_same_paris_borrower_block_preview(tmp_path):
    db, store = _setup(tmp_path)
    try:
        with db._lock, db._connect() as conn:
            _canonical(conn, "source-other-branch", branch="external-loan")
        report = _preview(store)
        assert report["status"] == "ambiguous_canonical_branch"
        assert report["review_reasons"] == [
            "historical_loan_does_not_attest_canonical_branch"
        ]
        _no_permission(report)
    finally:
        db.close()


def test_other_library_complete_receipt_same_paris_borrower_blocks_preview(tmp_path):
    first = _historical_acq("alice", "paris-a")
    second = _historical_acq("bob", "paris-a")
    second["acquisition_id"] = "acq-cross-library"
    db, store = _setup(tmp_path, acquisitions=[first, second])
    try:
        report = _preview(store)
        assert report["status"] == "ambiguous_library_destination"
        assert report["review_reasons"] == [
            "historical_adept_acquisition_in_other_library"
        ]
        _no_permission(report)
        assert report["recorded_adept_profile_id"] == "adobe-a"
    finally:
        db.close()


def test_multiple_completed_receipts_same_local_source_block_manual_selection(tmp_path):
    first = _historical_acq()
    second = _historical_acq()
    second["acquisition_id"] = "acq-paris-a-second-receipt"
    db, store = _setup(tmp_path, acquisitions=[first, second])
    try:
        report = _preview(store)
        assert report["status"] == "ambiguous_historical_acquisitions"
        assert report["review_reasons"] == [
            "multiple_completed_receipts_for_local_source"
        ]
        assert report["historical_evidence"] == "inventory_attested"
        _no_permission(report)
    finally:
        db.close()


def test_another_paris_borrower_receipt_does_not_block_selected_exact_source(tmp_path):
    first = _historical_acq()
    second = _historical_acq("bob", "paris-b")
    db, store = _setup(tmp_path, acquisitions=[first, second])
    try:
        report = _preview(store)
        assert report["status"] == "review_candidate"
        _no_permission(report)
    finally:
        db.close()


@pytest.mark.parametrize("invalid_id", [None, 42, ["PRIVATE"], {"ref": "PRIVATE"}])
def test_preview_cannot_select_historical_record_with_nontext_acquisition_id(
    tmp_path, invalid_id,
):
    historical = _historical_acq()
    historical["acquisition_id"] = invalid_id
    db, store = _setup(tmp_path, acquisitions=[historical])
    try:
        old_opaque = _opaque_ref("acquisition", invalid_id)
        result = _preview(store, acquisition=old_opaque)
        assert result["status"] == "selection_missing"
        assert result["review_reasons"] == [
            "exact_source_or_acquisition_missing"
        ]
        assert result["acquisition_ref"] is None
        _no_permission(result)
        assert "PRIVATE" not in repr(result)
    finally:
        db.close()
