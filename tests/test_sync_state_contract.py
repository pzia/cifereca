from __future__ import annotations

from pathlib import Path

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set(
        "identities",
        [
            {"id": "default", "label": "Hugues", "directory_name": "hugues", "preferences": {"reading_languages": ["fr"]}},
            {"id": "alex", "label": "Alex", "directory_name": "alex", "preferences": {"reading_languages": ["fr"]}},
        ],
    )
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())


def test_sync_run_roundtrip_preserves_latest_identity_state(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite3")
    assert storage.schema_version() == 14
    run_id = storage.sync_run_put({"identity_id": "alice", "scope": "opening", "status": "running", "started_at": 10})
    storage.sync_run_put({
        "sync_run_id": run_id,
        "identity_id": "alice",
        "scope": "opening",
        "status": "partial",
        "started_at": 10,
        "finished_at": 12,
        "new_acquisitions": 2,
        "attention_required": 1,
        "detail": {"providers": ["paris"]},
    })
    row = storage.latest_sync_run("alice")
    assert row is not None
    assert row["status"] == "partial"
    assert row["new_acquisitions"] == 2
    assert row["detail"]["providers"] == ["paris"]


def test_missing_active_loan_becomes_realized_without_touching_artifacts(tmp_path: Path):
    service = _service(tmp_path)
    try:
        loan = Loan(
            id="loan-1", provider="PNB", medium="digital", record_id="book-1", title="Book",
            source_provider="paris", source_credential="cred", source_account_id="reader",
            identity_id="default", due_at="2099-01-01T00:00:00Z",
        )
        service._persist_active_loans("default", [loan])
        assert len(service.storage.entitlements(state="active")) == 1
        # Empty/partial scans are not proof of return, even if reconciliation
        # is requested: account-scoped completeness must be supplied.
        result = service._persist_active_loans("default", [])
        assert result["returned_realized"] == 0
        assert len(service.storage.entitlements(state="active")) == 1
        result = service._persist_active_loans("default", [], reconcile_missing=True)
        assert result["returned_realized"] == 0
        assert len(service.storage.entitlements(state="active")) == 1
        # Synthetic exhaustive result for precisely this digital borrower.
        changed = service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default",
            complete_scopes={("paris", "cred", "reader")},
        )
        assert changed == 1
        assert len(service.storage.entitlements(state="realized")) == 1
    finally:
        service.close()


def test_opening_sync_persists_per_identity_status_on_partial_errors(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        service.accounts = [
            Account(display_name="P", unique_id="p", provider_id="paris", credential_id="p", provider_label="Paris", identity_id="default"),
            Account(display_name="K", unique_id="k", provider_id="kobo", credential_id="k", provider_label="Kobo", identity_id="alex"),
        ]
        loans_by_identity = {
            "default": [Loan(
                id="l1", provider="PNB", medium="digital", record_id="r1", title="Loan",
                source_provider="paris", source_credential="p", source_account_id="p",
                identity_id="default", ebook_url="https://example/acsm", due_at="2099-01-01T00:00:00Z",
            )],
            "alex": [],
        }
        monkeypatch.setattr(service, "_loans_for_account", lambda account, force=False: loans_by_identity[account.identity_id])
        monkeypatch.setattr(service, "_acquire_active_loan_automatically", lambda loan: {"status": "attention_required", "loan_id": loan.id})
        monkeypatch.setattr(service, "catalog_scan", lambda: {"scanned": 0})
        monkeypatch.setattr(service, "kobo_backup_enabled", lambda: False)
        enable_synthetic_paris_opening(service)
        summary = service.opening_sync()
        assert len(summary["identities"]) == 2
        first = next(row for row in summary["identities"] if row["identity_id"] == "default")
        assert first["attention_required"] == 1
        assert service.storage.latest_sync_run("default")["status"] == "partial"
        assert service.storage.latest_sync_run("alex")["status"] == "success"
    finally:
        service.close()


def test_dashboard_state_reports_latest_sync_without_remote_work(tmp_path: Path):
    service = _service(tmp_path)
    try:
        service.storage.sync_run_put({
            "identity_id": "default", "scope": "opening", "status": "success",
            "started_at": 10, "finished_at": 11, "new_acquisitions": 3,
        })
        cards = service.dashboard_state()
        assert [card["identity_id"] for card in cards] == ["default", "alex"]
        assert cards[0]["sync"]["new_acquisitions"] == 3
        assert cards[1]["sync"] is None
    finally:
        service.close()
