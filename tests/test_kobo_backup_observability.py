from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.kobo.vault import KoboVaultError
from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>Test</body></html>")


class BackupSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, manifest, unauthorized=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest)
        self.unauthorized = unauthorized

    def connect(self):
        self.connected = True
        self._accounts = [
            Account(
                display_name="Kobo",
                unique_id="account",
                provider_id="kobo",
                credential_id=self.credential_id,
                identity_id="default",
            )
        ]
        return self.accounts

    def close(self):
        self.connected = False

    def backup_manifest(self):
        return list(self._manifest)

    def cover_url(self, _raw):
        return None

    def normalize_item(self, raw, default_collection="library"):
        product_id = str(raw["product_id"])
        return SearchResult(
            id=f"kobo:{self.credential_id}:{product_id}",
            record_id=product_id,
            docbase="KOBO",
            medium="digital",
            title=str(raw.get("title") or product_id),
            author=None,
            type="Livre numérique Kobo",
            format=str(raw.get("format") or "EPUB"),
            publisher=None,
            published_at=None,
            description=None,
            thumbnail_url=None,
            friendly_url=None,
            scenario="KOBO",
            source_provider="kobo",
            source_credential=self.credential_id,
            content_type="ebook",
            source_collection=default_collection,
            owned=True,
            downloadable=True,
            revision_id=str(raw.get("revision_id") or "") or None,
            metadata={},
        )

    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
        display_profile=None,
    ):
        if self.unauthorized:
            raise KoboVaultError(
                "Kobo-Vault : Bearer token invalide",
                code="unauthorized",
                status_code=401,
            )
        return {
            "acquisition": {
                "candidates": [{
                    "transport": "https",
                    "method": "GET",
                    "url": f"https://cdn.example/{product_id}.epub",
                    "headers": {},
                    "format": "epub",
                    "drm_type": "SignedNoDrm",
                }]
            }
        }


def _service(tmp_path: Path, *, manifest, unauthorized=False):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [{
            "id": "default",
            "label": "Hugues",
            "directory_name": "hugues",
            "preferences": {"reading_languages": ["fr"]},
        }],
    )
    config.set("providers.accounts", [])

    service = LibraryService(
        config,
        Storage(tmp_path / "db.sqlite3"),
        Diagnostics(level="detailed"),
    )
    service.storage.provider_connection_put({
        "identity_id": "default",
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "label": "Kobo",
    })

    descriptor = ProviderDescriptor(
        id="kobo",
        display_name="Kobo",
        description="test",
        state="ready",
        capabilities=ProviderCapabilities(search=True, backup=True, personal_library=True),
    )
    session = BackupSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=manifest,
        unauthorized=unauthorized,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.catalog_scan = lambda *args, **kwargs: {"scanned": 0}
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.resolve_adept_profile = lambda **kwargs: ("", "test")
    service.runtime_diagnostic = lambda _plugin_id: {}
    service.kobo_adept_state = lambda **kwargs: {
        "available": True,
        "compatible": True,
        "operational": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "software_version": "test",
        "error": None,
    }
    return service, session


def test_vault_auth_error_aborts_batch_and_persists_compact_summary(tmp_path: Path, monkeypatch):
    manifest = [
        {
            "product_id": f"book-{index}",
            "revision_id": f"rev-{index}",
            "title": f"Book {index}",
            "format": "EPUB",
            "content_type": "ebook",
            "owned": True,
            "downloadable": True,
        }
        for index in range(5)
    ]
    service, _session = _service(tmp_path, manifest=manifest, unauthorized=True)
    monkeypatch.setattr(
        service,
        "execute_acquisition_request",
        lambda request: (_ for _ in ()).throw(AssertionError("runtime must not run after Vault 401")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["manifest_items"] == 5
        assert summary["attempted"] == 1
        assert summary["errors"] == 1
        assert summary["vault_errors"] == 1
        assert summary["integration_errors"] == 0
        assert summary["adept_errors"] == 0
        assert summary["aborted"] is True
        assert summary["remaining"] == 4
        assert summary["abort_reason"]["phase"] == "vault"
        assert summary["abort_reason"]["status_code"] == 401
        assert summary["error_summary"] == [{
            "phase": "vault",
            "code": "unauthorized",
            "status_code": 401,
            "message": "Kobo-Vault : Bearer token invalide",
            "count": 1,
        }]

        assert service.storage.backup_get("kobo", "vault-1", "book-0")["status"] == "error"
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None

        diag = service.diagnostics.snapshot()[-1]
        assert diag["operation"] == "backup sync"
        assert diag["detail"]["aborted"] is True
        assert diag["detail"]["error_summary"][0]["code"] == "unauthorized"
    finally:
        service.close()


def test_backup_diagnostic_aggregates_persisted_errors_without_network(tmp_path: Path):
    service, _session = _service(tmp_path, manifest=[])
    try:
        for index, message in enumerate([
            "Kobo-Vault : Bearer token invalide",
            "Kobo-Vault : Bearer token invalide",
            "Kobo-Vault ne fournit pas de candidat EPUB/PDF HTTP direct compatible",
        ]):
            service.storage.backup_put({
                "provider_id": "kobo",
                "credential_id": "vault-1",
                "item_id": f"book-{index}",
                "revision_id": f"rev-{index}",
                "title": f"Book {index}",
                "author": "Author",
                "format": "EPUB",
                "status": "error",
                "error": message,
            })

        service._sessions = {}
        service.kobo_adept_state = lambda **kwargs: (_ for _ in ()).throw(AssertionError("diagnostic must stay offline"))

        report = service.kobo_backup_diagnostic()

        assert report["items"] == 3
        assert report["statuses"] == {"error": 3}
        assert report["error_summary"][0] == {
            "message": "Kobo-Vault : Bearer token invalide",
            "count": 2,
        }
        assert len(report["error_examples"]) == 2
    finally:
        service.close()


def test_backup_emits_progress_events_through_successful_download(tmp_path: Path, monkeypatch):
    manifest = [{
        "product_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": True,
    }]
    service, _session = _service(tmp_path, manifest=manifest)

    def execute(request):
        target = Path(request["destination"])
        _write_epub(target)
        return {
            "ok": True,
            "operation": "download",
            "artifact": {
                "path": str(target),
                "format": "epub",
                "drm": "none",
                "size": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
        }

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    events: list[dict] = []
    try:
        summary = service.sync_kobo_backup(progress=events.append)

        assert summary["downloaded"] == 1
        names = [event.get("event") for event in events]
        assert names[0] == "adept_probe"
        assert "manifest_fetch" in names
        assert "manifest" in names
        assert "scan" in names
        assert "acquiring" in names
        assert "downloaded" in names
        assert names[-1] == "done"
    finally:
        service.close()

def test_pending_without_errors_is_not_persisted_as_completed_batch(tmp_path: Path):
    service, _session = _service(
        tmp_path,
        manifest=[{"product_id": "missing", "revision_id": "r1", "title": "Missing",
                   "format": "EPUB", "downloadable": True}],
    )
    service.kobo_adept_state = lambda **_kw: {
        "available": False, "compatible": False, "operational": False,
        "error": {"message": "Runtime absent"},
    }
    try:
        result = service.sync_kobo_backup()
        assert result["pending"] == 1
        assert result["errors"] == 0
        assert result["status"] == "attention_required"
        assert int(service.storage.metadata_get("kobo_backup_last_attempt_at")) > 0
        assert int(service.storage.metadata_get("kobo_backup_last_manifest_success_at")) > 0
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
        assert service.storage.metadata_get("kobo_backup_last_batch_status") == "attention_required"
        # Throttle a repeated startup, but never classify it as complete.
        assert not service._kobo_backup_due()
    finally:
        service.close()


def test_empty_successful_kobo_manifest_is_a_completed_batch(tmp_path: Path):
    service, _session = _service(tmp_path, manifest=[])
    try:
        result = service.sync_kobo_backup()
        assert result["sessions"] == 1
        assert result["manifests_fetched"] == 1
        assert result["status"] == "complete"
        assert int(service.storage.metadata_get("kobo_backup_last_success_at")) > 0
    finally:
        service.close()


def test_aborted_kobo_batch_retains_manifest_success_but_not_acquisition_completion(tmp_path: Path):
    service, _session = _service(
        tmp_path,
        manifest=[{"product_id": "forbidden", "revision_id": "r1",
                   "title": "Forbidden", "format": "EPUB"}],
        unauthorized=True,
    )
    try:
        result = service.sync_kobo_backup()
        assert result["status"] == "aborted"
        assert result["manifests_fetched"] == 1
        # A fetched manifest from an aborted source is not an authoritative
        # successful source snapshot, even when the fetch itself succeeded.
        assert not service.storage.metadata_get("kobo_backup_last_manifest_success_at")
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
    finally:
        service.close()


def test_existing_reconciliation_skip_does_not_claim_batch_completed(tmp_path: Path):
    service, _session = _service(
        tmp_path,
        manifest=[{"product_id": "book-1", "revision_id": "r1", "title": "Book",
                   "format": "EPUB", "downloadable": True}],
    )
    # Use a real existing candidate: on a branch independent from #1349,
    # the legacy recovery path clears a conflict only when no file remains.
    candidate = service._kobo_backup_target_dir("default") / "Book.epub"
    _write_epub(candidate)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "identity_id": "default",
        "item_id": "book-1", "revision_id": "r1", "title": "Book",
        "format": "EPUB", "status": "reconcile_conflict",
    })
    try:
        summary = service.sync_kobo_backup()
        assert summary["errors"] == 0
        assert summary["reconcile_skipped"] == 1
        assert summary["status"] == "attention_required"
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
    finally:
        service.close()


def test_kobo_checkpoint_records_source_progress_without_book_titles(tmp_path: Path):
    from cifereca.core.kobo_backup import _kobo_checkpoint_key

    service, _session = _service(
        tmp_path,
        manifest=[{"product_id": "private-product", "revision_id": "r1",
                   "title": "Private Book Name", "format": "EPUB", "downloadable": True}],
    )
    service.kobo_adept_state = lambda **_kw: {
        "available": False, "compatible": False, "operational": False,
        "error": {"message": "Runtime absent"},
    }
    try:
        report = service.sync_kobo_backup()
        raw = service.storage.metadata_get(_kobo_checkpoint_key("default", "vault-1"))
        checkpoint = json.loads(raw)
        assert checkpoint["processed"] == 1
        assert checkpoint["total"] == 1
        assert checkpoint["status"] == "attention_required"
        assert checkpoint["counts"]["pending"] == 1
        assert report["sources"][0]["credential_id"] == "vault-1"
        assert report["sources"][0]["status"] == "attention_required"
        assert "Private Book Name" not in raw
        assert "private-product" not in raw
        assert len(checkpoint["last_item_digest"]) == 16
    finally:
        service.close()


def test_kobo_checkpoint_stops_at_last_committed_item_on_unexpected_exception(tmp_path: Path, monkeypatch):
    from cifereca.core import kobo_backup

    service, _session = _service(
        tmp_path,
        manifest=[
            {"product_id": "first", "revision_id": "r1", "title": "First",
             "format": "EPUB", "downloadable": False},
            {"product_id": "second", "revision_id": "r1", "title": "Second",
             "format": "EPUB", "downloadable": True},
        ],
    )
    original = kobo_backup._refresh_manifest_record

    def interrupted(*args, **kwargs):
        if args[4] == "second":
            raise RuntimeError("simulated interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(kobo_backup, "_refresh_manifest_record", interrupted)
    try:
        import pytest
        with pytest.raises(RuntimeError, match="simulated interruption"):
            service.sync_kobo_backup()
        checkpoint = json.loads(
            service.storage.metadata_get(kobo_backup._kobo_checkpoint_key("default", "vault-1"))
        )
        assert checkpoint["processed"] == 1
        assert checkpoint["status"] == "interrupted"
        assert checkpoint["counts"]["scanned"] == 2
        assert "second" not in str(checkpoint)
    finally:
        service.close()


def test_kobo_checkpoint_records_aborted_source_before_exiting_batch(tmp_path: Path):
    from cifereca.core.kobo_backup import _kobo_checkpoint_key

    service, _session = _service(
        tmp_path, unauthorized=True,
        manifest=[{"product_id": f"book-{index}", "revision_id": "r1",
                   "title": f"Book {index}", "format": "EPUB"} for index in range(3)],
    )
    try:
        report = service.sync_kobo_backup()
        checkpoint = json.loads(
            service.storage.metadata_get(_kobo_checkpoint_key("default", "vault-1"))
        )
        assert report["aborted"] is True
        assert report["sources"][0]["status"] == "aborted"
        assert checkpoint["status"] == "aborted"
        assert checkpoint["processed"] == 1
        assert checkpoint["total"] == 3
        assert checkpoint["counts"]["errors"] == 1
    finally:
        service.close()


def test_malformed_manifest_does_not_advance_authoritative_success_timestamp(tmp_path: Path):
    """#1348: fetched != authoritative when one manifest entry is invalid."""
    service, _session = _service(
        tmp_path,
        manifest=[{"title": "Missing product ID", "format": "EPUB"}],
    )
    try:
        service.storage.metadata_set("kobo_backup_last_manifest_success_at", 12345)
        report = service.sync_kobo_backup()
        assert report["manifests_fetched"] == report["sessions"] == 1
        assert report["status"] == "partial"
        assert report["malformed_manifest_items"] > 0
        assert int(service.storage.metadata_get("kobo_backup_last_manifest_success_at")) == 12345
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
    finally:
        service.close()


def test_authoritative_manifest_can_succeed_with_acquisition_attention(tmp_path: Path):
    """#1348: a fully read manifest is distinct from completed acquisition."""
    service, _session = _service(
        tmp_path,
        manifest=[{"product_id": "waiting", "revision_id": "r1",
                   "title": "Waiting", "format": "EPUB", "downloadable": True}],
    )
    service.kobo_adept_state = lambda **_kw: {
        "available": False, "compatible": False, "operational": False,
        "error": {"message": "Runtime absent"},
    }
    try:
        report = service.sync_kobo_backup()
        assert report["status"] == "attention_required"
        assert int(service.storage.metadata_get("kobo_backup_last_manifest_success_at")) > 0
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
    finally:
        service.close()


def test_duplicate_manifest_does_not_advance_authoritative_success_timestamp(tmp_path: Path):
    """#1348: duplicate product IDs cannot certify a complete snapshot."""
    service, _session = _service(
        tmp_path,
        manifest=[
            {"product_id": "same", "revision_id": "r1", "title": "First", "format": "EPUB"},
            {"product_id": "same", "revision_id": "r2", "title": "Second", "format": "EPUB"},
        ],
    )
    try:
        service.storage.metadata_set("kobo_backup_last_manifest_success_at", 12345)
        report = service.sync_kobo_backup()
        assert report["manifests_fetched"] == report["sessions"] == 1
        assert report["duplicate_manifest_items"] == 2
        assert report["status"] == "partial"
        assert int(service.storage.metadata_get("kobo_backup_last_manifest_success_at")) == 12345
        assert not service.storage.metadata_get("kobo_backup_last_success_at")
    finally:
        service.close()
