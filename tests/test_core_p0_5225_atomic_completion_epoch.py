"""#5225: durable epoch check is in the same SQLite write as completion.

No real network/ADEPT or library mutation; fixtures own their SQLite/files.
"""
from dataclasses import replace
import sqlite3
from types import SimpleNamespace as NS

import pytest

from cifereca import services
from cifereca.core import loan_completion_epoch as fence
from cifereca.core.loan_source_epoch import SourceEpochLease
from tests.test_automatic_loan_admission import _setup


def _record(lease, status="complete"):
    return {
        "acquisition_id": "synthetic-loan-receipt",
        "identity_id": lease.identity_id, "provider_id": lease.provider_id,
        "credential_id": lease.credential_id, "account_id": lease.account_id,
        "item_id": lease.record_id or lease.loan_id, "status": status,
        "strategy": "loan_auto", "executor": "provider",
        "metadata": {"provider_loan_id": lease.loan_id},
    }


def _rotate(service):
    for enabled in (False, True):
        service.storage.provider_connection_put({
            "provider_id": "paris", "credential_id": "source-a",
            "identity_id": "default", "enabled": enabled,
        })


def test_epoch_rotated_at_actual_storage_entry_never_commits_complete(tmp_path, monkeypatch):
    service, loan = _setup(tmp_path)
    candidate = tmp_path / "synthetic.pdf"
    candidate.write_bytes(b"synthetic")
    real_put = service.storage.acquisition_put
    try:
        monkeypatch.setattr(services.loan_provider_delivery, "acquire_provider_loan",
                            lambda *_a, **_kw: NS(path=candidate, kind="direct", acsm_path=None))
        monkeypatch.setattr(services.loan_provider_delivery, "index_local_loan_artifact",
                            lambda *_a, **_kw: (NS(media_format="pdf", protected=False),
                                                {"status": "indexed"}))
        events = []
        def put(payload, **kwargs):
            if payload.get("status") == "complete":
                assert type(kwargs["completion_lease"]) is SourceEpochLease
                events.append("rotated")
                _rotate(service)
            return real_put(payload, **kwargs)
        monkeypatch.setattr(service.storage, "acquisition_put", put)
        result = service._acquire_active_loan_automatically_guarded(loan)
        assert events == ["rotated"]
        assert result["status"] == "attention_required" and result["reason"] == "source_scope_changed"
        row, = service.storage.acquisitions(provider_id="paris")
        assert row["status"] == "acquiring"
        assert candidate.read_bytes() == b"synthetic"
    finally:
        service.close()


@pytest.mark.parametrize("key,value", [
    ("identity_id", "alex"), ("account_id", None), ("item_id", "other-edition"),
    ("metadata", {"provider_loan_id": "other-loan"}),
])
def test_guarded_receipt_must_match_pinned_coordinates(tmp_path, key, value):
    service, loan = _setup(tmp_path)
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert lease is not None
        initial = _record(lease, "acquiring")
        aid = service.storage.acquisition_put(initial)
        saved = service.storage.acquisition_get(aid)
        with pytest.raises(fence.CompletionEpochRevoked):
            service.storage.acquisition_put({**_record(lease), key: value}, completion_lease=lease)
        assert service.storage.acquisition_get(aid) == saved
    finally:
        service.close()


def test_durable_connection_writer_cannot_enter_guarded_transaction(tmp_path, monkeypatch):
    service, loan = _setup(tmp_path)
    other = sqlite3.connect(service.storage.path, timeout=0)
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert lease is not None
        aid = service.storage.acquisition_put(_record(lease, "acquiring"))
        original = fence.require_completion_epoch
        calls = []
        def contested(db, lease):
            original(db, lease)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE provider_connections SET enabled=0 WHERE provider_id='paris'")
            other.rollback()
            calls.append(True)
        monkeypatch.setattr(fence, "require_completion_epoch", contested)
        assert service.storage.acquisition_put(_record(lease), completion_lease=lease) == aid
        assert calls == [True, True]
        assert service.storage.acquisition_get(aid)["status"] == "complete"
        assert lease.current(service.storage, loan)
    finally:
        other.close()
        service.close()


def test_postwrite_epoch_mismatch_rolls_back_receipt_and_trigger(tmp_path):
    service, loan = _setup(tmp_path)
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert lease is not None
        aid = service.storage.acquisition_put(_record(lease, "acquiring"))
        before = service.storage.acquisition_get(aid)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "CREATE TRIGGER synthetic_rotate_on_complete AFTER UPDATE OF status "
                "ON acquisition_ledger WHEN NEW.status='complete' BEGIN "
                "UPDATE provider_connection_epochs SET epoch=epoch+1 "
                "WHERE provider_id='paris' AND credential_id='source-a'; END"
            )
        with pytest.raises(fence.CompletionEpochRevoked):
            service.storage.acquisition_put(_record(lease), completion_lease=lease)
        assert service.storage.acquisition_get(aid) == before
        assert lease.current(service.storage, loan)
    finally:
        service.close()
