"""P0 #3171: exact, safe reason for a blocked offline Numilog index resume.

All purchases/EPUB bytes, receipts and accounts are synthetic. Real SQLite,
same locks/provenance; never downloads, calls a provider or moves user files.
"""
import pytest

from cifereca.core.numilog_materialization_checkpoint import (
    NumilogIndexResumeRejected,
)
from tests.test_core_p0_numilog_acsm_provenance import acsm_case, _delivered
from tests.test_numilog_owned_library_download import case  # fixture for acsm_case


@pytest.mark.parametrize(("fault", "reason"), [
    ("manual_receipt_missing", "manual_receipt_missing"),
    ("manual_wrong_owner", "manual_receipt_mismatch"),
    ("catalogue_conflict", "catalogue_conflict"),
    ("shared_artifact_elsewhere", "artifact_shared_elsewhere"),
    ("competing_purchase_receipt", "competing_receipts"),
])
def test_atomic_refusal_identifies_failed_gate_without_changing_evidence(
    acsm_case, tmp_path, fault, reason,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    placement = storage.placement_for_path(path)
    original = path.read_bytes()

    if fault == "manual_receipt_missing":
        with storage._connect() as db:
            db.execute("DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                       (case.manual_id,))
    elif fault == "manual_wrong_owner":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET identity_id='other' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif fault == "catalogue_conflict":
        storage.catalog_entry_put({
            "catalog_entry_id": "synthetic-second-row",
            "artifact_id": placement["artifact_id"],
            "placement_id": placement["placement_id"],
            "identity_id": case.account.identity_id,
            "provider_id": "numilog", "item_id": "other-item",
            "local_state": "local",
        })
    elif fault == "shared_artifact_elsewhere":
        # Distinct paths with the same SHA and the SAME owner remain ambiguous.
        # Registered foreign owners are covered by the positive regression.
        for owner in ("foreign-1", "foreign-2"):
            another = tmp_path / (owner + ".epub")
            another.write_bytes(original)
            storage.placement_put({
                "placement_id": owner,
                "artifact_id": placement["artifact_id"], "path": str(another),
                "identity_id": case.account.identity_id, "provider_id": "numilog",
                "source_item_id": "different-edition", "state": "present",
            })
    else:
        row = storage.acquisition_get(receipt_id)
        storage.acquisition_put({**row, "acquisition_id": "other-purchase-receipt"})

    # Preserve the old bool API; enhanced refusal comes from the SAME
    # SQLite transaction, without a speculative second provenance scan.
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path),
    ) is False
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path), diagnose=True,
    ) == reason

    with pytest.raises(NumilogIndexResumeRejected) as raised:
        case.service.resume_numilog_materialization(receipt_id)
    assert raised.value.reason_code == (
        "receipt_conflict" if fault == "competing_purchase_receipt" else reason
    )
    msg = str(raised.value)
    assert "PRIVATE" not in msg and str(path) not in msg
    assert receipt_id not in msg and case.item.record_id not in msg
    assert path.read_bytes() == original
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert case.deliveries == [case.item.record_id]


def test_atomic_success_retains_exact_receipt_and_zero_new_network(acsm_case):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    assert storage.numilog_checkpoint_adopt_acsm_placement(
        receipt_id, expected_path=str(path), diagnose=True,
    ) == "adopted"
    result = case.service.resume_numilog_materialization(receipt_id)
    assert result.path == path
    assert result.indexed["status"] == "indexed"
    assert storage.acquisition_get(receipt_id)["status"] == "complete"
    assert case.deliveries == [case.item.record_id]


@pytest.mark.parametrize(("raw", "expected"), [
    ("private/unexpected", "adoption_unavailable"),
    ({"path": "/private/"}, "adoption_unavailable"),
    ("artifact_shared_elsewhere", "artifact_shared_elsewhere"),
])
def test_refusal_codes_are_an_exact_allowlist(raw, expected):
    refusal = NumilogIndexResumeRejected(raw)
    assert refusal.reason_code == expected
    assert "private" not in str(refusal).lower()


@pytest.mark.parametrize(("change", "code"), [
    ("bytes", "file_changed"),
    ("missing", "file_changed"),
    ("selected_other_identity", "selected_identity_changed"),
    ("account_rebound", "account_changed"),
    ("ledger_duplicated", "receipt_conflict"),
    ("directory_changed", "root_changed"),
    ("migration_pending", "directory_migration_pending"),
])
def test_resume_pre_adoption_refusal_names_exact_safe_gate(
    case, tmp_path, monkeypatch, change, code,
):
    from types import SimpleNamespace as NS

    acquired = case.service.acquire_direct_item(case.item)
    receipt = acquired.metadata["numilog_checkpoint_id"]
    storage = case.service.storage
    if change == "bytes":
        acquired.path.write_bytes(b"synthetic different bytes")
    elif change == "missing":
        acquired.path.unlink()
    elif change == "selected_other_identity":
        case.service.current_identity = NS(identity_id="foreign")
    elif change == "account_rebound":
        storage.provider_connection_put({
            "provider_id": "numilog",
            "credential_id": case.account.credential_id,
            "identity_id": "foreign",
        })
    elif change == "ledger_duplicated":
        row = storage.acquisition_get(receipt)
        storage.acquisition_put({**row, "acquisition_id": "duplicated-receipt"})
    elif change == "directory_changed":
        case.service.config.set("library.directory", str(tmp_path / "new-root"))
    else:
        storage.metadata_set("identity-directory-migration-v1", "pending")
    monkeypatch.setattr(
        case.service, "catalog_index_path",
        lambda *_args, **kwargs: pytest.fail("unverified receipt reached index"),
    )
    with pytest.raises(NumilogIndexResumeRejected) as raised:
        case.service.resume_numilog_materialization(receipt)
    assert raised.value.reason_code == code
    assert receipt not in str(raised.value)
    assert case.item.record_id not in str(raised.value)
    assert storage.acquisition_get(receipt)["status"] == "attention_required"
    assert case.calls == [case.item.record_id]


@pytest.mark.parametrize(("fault", "phase", "code"), [
    ("lock", "lock", "acquisition_busy"),
    ("index_refused", "index", "index_rejected"),
    ("index_exception", "index", "indexing_failed"),
    ("index_wrong_artifact", "revalidate", "artifact_mismatch"),
    ("complete_refused", "complete", "finalization_failed"),
    ("complete_exception", "complete", "finalization_failed"),
])
def test_resume_failure_is_correlated_and_keeps_delivered_bytes_pending(
    case, monkeypatch, caplog, fault, phase, code,
):
    import logging
    from contextlib import nullcontext
    from cifereca.diagnostic_task_context import diagnostic_task_scope
    from cifereca.locking import InterProcessLock
    from cifereca.paths import acquisition_lock_file

    acquired = case.service.acquire_direct_item(case.item)
    receipt_id = acquired.metadata["numilog_checkpoint_id"]
    original = acquired.path.read_bytes()
    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic SQLite failure /original/path")
    if fault == "index_refused":
        monkeypatch.setattr(case.service, "catalog_index_path", lambda *_a, **_k: {
            "status": "invalid", "reason": "synthetic-invalid-opf", "path": str(acquired.path),
        })
    elif fault == "index_exception":
        monkeypatch.setattr(case.service, "catalog_index_path", broken)
    elif fault == "index_wrong_artifact":
        monkeypatch.setattr(case.service, "catalog_index_path", lambda *_a, **_k: {
            "status": "indexed", "artifact_id": "sha256:wrong",
        })
    elif fault == "complete_refused":
        monkeypatch.setattr(case.service.storage, "numilog_checkpoint_complete", lambda *_a, **_k: False)
    elif fault == "complete_exception":
        monkeypatch.setattr(case.service.storage, "numilog_checkpoint_complete", broken)
    held = InterProcessLock(acquisition_lock_file()) if fault == "lock" else nullcontext()
    with caplog.at_level(logging.WARNING, logger="cifereca"), held, diagnostic_task_scope():
        with pytest.raises(NumilogIndexResumeRejected) as raised:
            case.service.resume_numilog_materialization(receipt_id)
    assert raised.value.reason_code == code
    events = [r for r in caplog.records if r.getMessage().startswith("TRACE Numilog index resume")]
    assert len(events) == 1
    fields = events[0]._cifereca_journal_payload["detail"]
    assert fields["phase"] == phase and fields["code"] == code
    assert fields["task_kind"] == "index" and fields["acquisition_id"] == receipt_id
    assert len(fields["task_ref"]) == 12
    assert events[0].exc_info is not None
    if fault == "index_refused":
        assert fields["index_result"]["reason"] == "synthetic-invalid-opf"
        assert fields["index_result"]["path"] == str(acquired.path)
    if fault.endswith("exception"):
        assert "synthetic SQLite failure /original/path" in caplog.text
        assert "original/path" not in str(raised.value)
    assert acquired.path.read_bytes() == original
    assert case.service.storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert case.calls == [case.item.record_id]
    # Once the local fault is resolved, two recoveries reuse the same file.
    # A failed finalization may already have indexed it; that is recoverable.
    monkeypatch.undo()
    for _ in range(2):
        result = case.service.resume_numilog_materialization(receipt_id)
        assert result.path == acquired.path and result.indexed["status"] == "indexed"
        assert acquired.path.read_bytes() == original
        assert case.service.storage.acquisition_get(receipt_id)["status"] == "complete"
    assert case.calls == [case.item.record_id]


def test_resume_cancellation_is_not_a_failure(case, monkeypatch, caplog):
    from cifereca.plugin_api import Cancelled
    acquired = case.service.acquire_direct_item(case.item)
    receipt_id = acquired.metadata["numilog_checkpoint_id"]
    def cancelled(*_args, **_kwargs):
        raise Cancelled()
    monkeypatch.setattr(case.service, "catalog_index_path", cancelled)
    with pytest.raises(Cancelled):
        case.service.resume_numilog_materialization(receipt_id)
    assert not any(r.getMessage().startswith("TRACE Numilog index resume") for r in caplog.records)
    assert case.service.storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert case.calls == [case.item.record_id]


def test_optional_journal_failure_keeps_exact_rejection(case, monkeypatch):
    acquired = case.service.acquire_direct_item(case.item)
    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic log handler failure")
    monkeypatch.setattr("cifereca.diagnostics._core_journal_record", broken)
    acquired.path.unlink()
    with pytest.raises(NumilogIndexResumeRejected) as raised:
        case.service.resume_numilog_materialization(acquired.metadata["numilog_checkpoint_id"])
    assert raised.value.reason_code == "file_changed"
    assert case.calls == [case.item.record_id]


def test_index_task_trace_keeps_actual_kind(caplog):
    import logging
    from tests.test_core_p0_981_redacted_task_traces import _runner
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Reprise Numilog", lambda: None, kind="index", origin="user")
            flush()
        assert "kind=index" in caplog.text
        assert "kind=other" not in caplog.text
        assert displayed[0][1][4] == "index"
    finally:
        runner.close()
