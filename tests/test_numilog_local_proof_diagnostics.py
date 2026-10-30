"""Existing Numilog checks explain their refusal without new I/O or authority."""
import hashlib
import json
import logging

import pytest

from cifereca.core import numilog_local_preview as preview
from cifereca.core import numilog_materialization_startup as startup
from cifereca.core import numilog_startup_diagnostics as diagnostics
from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_3171_numilog_completed_startup import _completed_purchase
from tests.test_numilog_owned_library_download import case


PREFIX = "TRACE numilog local_presence_check "


def reports(caplog):
    return [(r, json.loads(r.getMessage()[len(PREFIX):]))
            for r in caplog.records if r.getMessage().startswith(PREFIX)]


def rights_scope(storage):
    # The existing authenticated inventory legitimately refreshes metadata.
    # This diagnostic must not create/reassign/revoke an ownership right.
    fields = ("entitlement_id", "identity_id", "credential_id", "account_id",
              "item_id", "entitlement_type", "state", "loan_id", "expires_at")
    return [tuple(row.get(key) for key in fields) for row in storage.entitlements()]


@pytest.mark.parametrize("damage,gate", [
    ("none", "verified"),
    ("nested", "verified"),
    ("missing", "file_unavailable"),
    ("changed_bytes", "file_fingerprint_mismatch"),
    ("foreign_receipt", "receipt_incomplete_or_scope_mismatch"),
    ("pending_receipt", "receipt_incomplete_or_scope_mismatch"),
    ("duplicate_receipt", "rights_or_receipt_ambiguous"),
])
def test_startup_records_exact_existing_gate_and_keeps_download_decision(case, caplog, damage, gate):
    broker, sid, path = _completed_purchase(case)
    storage = case.service.storage
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if damage == "nested":
        nested = path.parent / "acsm-historical" / "deep" / path.name
        nested.parent.mkdir(parents=True)
        path.rename(nested)
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE placements SET path=? WHERE path=?", (str(nested), str(path)))
        path = nested
    elif damage == "missing":
        path.unlink()
    elif damage == "changed_bytes":
        path.write_bytes(b"%PDF-1.7\nDifferent actual bytes\n")
    elif damage in {"foreign_receipt", "pending_receipt"}:
        with storage._lock, storage._connect() as db:
            if damage == "foreign_receipt":
                db.execute("UPDATE acquisition_ledger SET account_id='other-buyer' WHERE provider_id='numilog'")
            else:
                db.execute("UPDATE acquisition_ledger SET status='attention_required' WHERE provider_id='numilog'")
    elif damage == "duplicate_receipt":
        storage.acquisition_put(dict(
            acquisition_id="second-exact-item-receipt", provider_id="numilog",
            identity_id=case.account.identity_id, credential_id=case.account.credential_id,
            account_id=case.account.unique_id, item_id=case.item.record_id,
            status="attention_required",
        ))
    before = (storage.acquisitions(), storage.placements(), rights_scope(storage))
    caplog.set_level(logging.INFO, logger="cifereca")
    with diagnostic_task_scope():
        task = diagnostic_task_reference()
        result = startup.execute(broker, case.account.identity_id, "startup")
    rows = reports(caplog)
    detail = next(d for _, d in rows if d["gate"] == gate)
    assert detail["task_ref"] == task
    for record, facts in rows:
        assert record._cifereca_journal_payload["detail"] == facts
        assert record._cifereca_journal_payload["operation"] == "local_presence_check"
    assert detail["item_id"] == case.item.record_id and detail["source_id"] == sid
    assert (detail["identity_id"], detail["credential_id"], detail["account_id"]) == (
        case.account.identity_id, case.account.credential_id, case.account.unique_id,
    )
    assert result.facts["attempted"] == result.facts["downloaded"] == 0
    assert result.ok == (damage in {"none", "nested"})
    assert (storage.acquisitions(), storage.placements(), rights_scope(storage)) == before
    if damage in {"none", "nested", "changed_bytes", "missing"}:
        physical = next(d for _, d in rows if "path" in d)
        assert physical["path"] == str(path)
        if damage == "changed_bytes":
            assert physical["expected_sha256"] == digest
            assert physical["observed_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        elif damage == "missing":
            assert physical["observed_sha256"] is None
    if damage not in {"none", "nested"}:
        assert all(d["gate"] != "verified" for _, d in rows)


def test_receipt_change_after_hash_is_not_reported_as_verified(case, caplog, monkeypatch):
    broker, sid, path = _completed_purchase(case)
    real_hash = preview._read_hash_within

    def raced(*args):
        observed = real_hash(*args)
        with case.service.storage._lock, case.service.storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET status='attention_required' WHERE provider_id='numilog'")
        return observed

    monkeypatch.setattr(preview, "_read_hash_within", raced)
    caplog.set_level(logging.INFO, logger="cifereca")
    assert not startup._completed_local_purchase_verified(
        broker, case.account, sid, case.item.record_id, source_current=lambda: True,
    )
    gates = [d["gate"] for _, d in reports(caplog)]
    assert "file_fingerprint_verified" in gates
    assert "evidence_changed_after_file_check" in gates
    assert "verified" not in gates
    assert path.is_file()


@pytest.mark.parametrize("phase", ["receipt", "file"])
def test_original_local_exception_is_journaled_without_a_new_download(case, caplog, monkeypatch, phase):
    broker, sid, path = _completed_purchase(case)
    cause = RuntimeError("Synthetic storage or file reader unavailable")
    def fail(*args, **kwargs):
        raise cause
    if phase == "receipt":
        monkeypatch.setattr(case.service.storage, "numilog_purchase_preflight_evidence", fail)
    else:
        monkeypatch.setattr(preview, "_read_hash_within", fail)
    caplog.set_level(logging.INFO, logger="cifereca")
    assert not startup._completed_local_purchase_verified(
        broker, case.account, sid, case.item.record_id, source_current=lambda: True,
    )
    record, detail = next((r, d) for r, d in reports(caplog)
                          if d["gate"] == ("verification_failed" if phase == "receipt" else "preview_failed"))
    assert record.exc_info[1] is cause
    assert detail["item_id"] == case.item.record_id
    assert path.is_file()


def test_cancellation_during_file_check_propagates_without_failure_reclassification(case, caplog, monkeypatch):
    broker, sid, path = _completed_purchase(case)
    def cancel(*args):
        raise Cancelled("synthetic cancellation")
    monkeypatch.setattr(preview, "_read_hash_within", cancel)
    caplog.set_level(logging.INFO, logger="cifereca")
    with pytest.raises(Cancelled):
        startup._completed_local_purchase_verified(
            broker, case.account, sid, case.item.record_id, source_current=lambda: True,
        )
    assert not reports(caplog)
    assert path.is_file()


def test_optional_log_handler_failure_does_not_change_verified_presence(case, monkeypatch):
    broker, sid, path = _completed_purchase(case)
    def broken_handler(*args, **kwargs):
        raise RuntimeError("synthetic log sink failure")
    monkeypatch.setattr(diagnostics._LOG, "log", broken_handler)
    assert startup._completed_local_purchase_verified(
        broker, case.account, sid, case.item.record_id, source_current=lambda: True,
    )
    assert path.is_file()
