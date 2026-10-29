"""Archive is a provider delivery fact, never an acquisition error or lost right."""
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.kobo_backup import safe_kobo_backup_report
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_fault_isolation import _service, _ready_adept_state, _write_epub


def test_archived_409_is_persisted_and_not_retried_without_explicit_unarchive(tmp_path, monkeypatch):
    manifest = [{"product_id": "book-1", "title": "Book", "revision_id": "r1", "format": "EPUB"}]
    service, session = _service(tmp_path, manifest)
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_: _ready_adept_state())
    calls = []

    def archived(item, target):
        calls.append(item.record_id)
        raise KoboVaultError("archived_book", code="archived_book", status_code=409)

    monkeypatch.setattr(service, "_execute_kobo_acquisition", archived)
    try:
        first = service.sync_kobo_backup_account("vault-1")
        second = service.sync_kobo_backup_account("vault-1")
        assert calls == ["book-1"]
        assert first["attempted"] == 1 and second["attempted"] == 0
        for summary in (first, second):
            assert summary["status"] == "complete"
            assert summary["skipped_archived"] == 1
            assert summary["errors"] == summary["attention_required"] == 0
            assert summary["vault_errors"] == summary["integration_errors"] == 0
            assert summary["sources"][0]["skipped_archived"] == 1
            assert summary["error_summary"] == []
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "skipped_archived" and row["attempt_count"] == 1
        report = safe_kobo_backup_report(service.kobo_backup_diagnostic())
        assert report["sources"][0]["skipped_archived"] == 1
        assert report["error_count"] == report["attention_count"] == 0
        assert service.storage.placements() == []
        assert all(right["state"] == "active" for right in service.storage.entitlements())

        # Explicit fresh archived=False lifts only this provider delivery fact.
        session._manifest[0]["archived"] = False
        def available(item, target):
            calls.append(item.record_id)
            path = Path(target) / "book.epub"
            _write_epub(path)
            return SimpleNamespace(path=path, account_id="account", metadata={})
        monkeypatch.setattr(service, "_execute_kobo_acquisition", available)
        resumed = service.sync_kobo_backup_account("vault-1")
        assert resumed["downloaded"] == 1 and resumed["skipped_archived"] == 0
        assert len(calls) == 2
    finally:
        service.close()


def test_archived_manifest_preserves_received_file_right_and_placement(tmp_path, monkeypatch):
    service, session = _service(tmp_path, [{
        "product_id": "book-1", "title": "Book", "revision_id": "r1", "format": "EPUB",
    }])
    monkeypatch.setattr(service, "kobo_adept_state", lambda **_: _ready_adept_state())
    def acquire(item, target):
        path = Path(target) / "book.epub"
        _write_epub(path)
        return SimpleNamespace(path=path, account_id="account", metadata={})
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        assert service.sync_kobo_backup_account("vault-1")["downloaded"] == 1
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        placements = service.storage.placements()
        rights = service.storage.entitlements()
        data = Path(before["local_path"]).read_bytes()
        session._manifest[0].update(archived=True, downloadable=False)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_: (_ for _ in ()).throw(AssertionError("no second GET")))
        for _ in range(2):
            summary = service.sync_kobo_backup_account("vault-1")
            assert summary["skipped_archived"] == 1 and summary["attempted"] == 0
            assert summary["status"] == "complete"
            after = service.storage.backup_get("kobo", "vault-1", "book-1")
            assert (after["local_path"], after["sha256"], after["status"]) == (
                before["local_path"], before["sha256"], "complete")
            assert service.storage.placements() == placements
            assert [(r["entitlement_id"], r["state"]) for r in service.storage.entitlements()] == [
                (r["entitlement_id"], r["state"]) for r in rights]
            assert Path(after["local_path"]).read_bytes() == data
    finally:
        service.close()
