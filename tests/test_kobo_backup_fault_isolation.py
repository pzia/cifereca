from __future__ import annotations

import hashlib
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

    def __init__(self, *args, manifest, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest)
        self.resolve_calls: list[tuple[str, str]] = []

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
        self.resolve_calls.append((product_id, mode))
        if product_id == "bad":
            if mode == "delegated":
                raise KoboVaultError(
                    "Kobo-Vault : scope insuffisant",
                    code="insufficient_scope",
                    status_code=403,
                )
            return {
                "acquisition": {
                    "candidates": [{
                        "transport": "https",
                        "method": "GET",
                        "url": "https://cdn.example/bad",
                        "headers": {},
                        "format": "audio",
                        "drm_type": "KDRM",
                    }]
                }
            }

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


def _service(tmp_path: Path, manifest):
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
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
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
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.catalog_scan = lambda *args, **kwargs: {"scanned": 0}
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.resolve_adept_profile = lambda **kwargs: ("", "test")
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service, session


def _ready_adept_state():
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "acsm_available": False,
        "kobo_delegated_available": True,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "software_version": "test",
        "error": None,
    }


def test_missing_delegate_scope_is_item_error_and_does_not_abort_batch(tmp_path: Path, monkeypatch):
    service, session = _service(
        tmp_path,
        [
            {"product_id": "bad", "revision_id": "r1", "title": "Bad", "format": "EPUB"},
            {"product_id": "good", "revision_id": "r2", "title": "Good", "format": "EPUB"},
        ],
    )
    monkeypatch.setattr(service, "kobo_adept_state", lambda **kwargs: _ready_adept_state())

    calls: list[str] = []

    def execute(request):
        target = Path(request["destination"])
        calls.append(target.name)
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
    try:
        summary = service.sync_kobo_backup()

        assert summary["errors"] == 0
        assert summary["items_blocked_by_scope"] == 1
        assert summary["attention_required"] == 1
        assert summary["aborted"] is False
        assert summary["downloaded"] == 1
        assert calls
        assert ("bad", "delegated") in session.resolve_calls

        bad = service.storage.backup_get("kobo", "vault-1", "bad")
        good = service.storage.backup_get("kobo", "vault-1", "good")
        assert bad and bad["status"] == "attention_required"
        assert bad["attention_code"] == "missing_delegation_scope"
        assert "kobo.acquisition.delegate" in str(bad["error"])
        assert good and good["status"] == "complete"
    finally:
        service.close()


def test_missing_adept_runtime_keeps_item_pending_and_clears_legacy_job_id(tmp_path: Path, monkeypatch):
    service, _session = _service(
        tmp_path,
        [{"product_id": "book-1", "revision_id": "rev-1", "title": "Book One", "format": "EPUB"}],
    )
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "item_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "author": "Author",
        "format": "EPUB",
        "status": "queued",
        "job_id": "job-old",
    })
    monkeypatch.setattr(
        service,
        "kobo_adept_state",
        lambda **kwargs: {
            "available": False,
            "compatible": False,
            "operational": False,
            "acsm_available": False,
            "kobo_delegated_available": False,
            "kobo_pinned_delivery_candidate": False,
            "kobo_separate_content_keys_replay": False,
            "kobo_kdrm_available": False,
            "kobo_formats": [],
            "contract_version": None,
            "software_version": None,
            "error": {"code": "runtime_plugin_unavailable", "message": "Kobo-Adept indisponible"},
        },
    )
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime must not be called")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["pending"] == 1
        assert summary["attempted"] == 0
        assert summary["aborted"] is False

        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "pending_acquisition"
        assert row["job_id"] is None
        assert "Adept" in str(row["error"])
    finally:
        service.close()


def test_real_plugin_error_only_probes_denied_delegate_once_per_session(tmp_path: Path, monkeypatch):
    """Exercise the actual plugin exception, not the Core homonym."""
    import importlib.util
    import sys

    source = Path(__file__).resolve().parents[1] / "src/plugins/provider-kobo/cifereca_kobo_vault.py"
    # Direct-file imports need sibling provider modules on the scoped test path.
    monkeypatch.syspath_prepend(str(source.parent))
    spec = importlib.util.spec_from_file_location("test_real_cifereca_kobo_vault", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.KoboVaultError is not KoboVaultError

    manifest = [
        {"product_id": f"bad{i}", "revision_id": "r1", "title": f"Bad{i}", "format": "EPUB"}
        for i in range(4)
    ] + [{"product_id": "good", "revision_id": "r1", "title": "Good", "format": "EPUB"}]
    service, session = _service(tmp_path, manifest)
    monkeypatch.setattr(service, "kobo_adept_state", lambda **kwargs: _ready_adept_state())
    delegated_calls = []
    initial_resolver = session.resolve_acquisition

    def resolver(product_id, *, revision_id=None, mode="descriptor", display_profile=None):
        if product_id.startswith("bad"):
            if mode == "delegated":
                delegated_calls.append(product_id)
                raise module.KoboVaultError("Kobo-Vault : forbidden", code="forbidden", status_code=403)
            return {"acquisition": {"candidates": [{
                "transport": "https", "method": "GET",
                "url": "https://cdn.example/unusable", "format": "audio",
                "drm_type": "KDRM",
            }]}}
        return initial_resolver(product_id, revision_id=revision_id, mode=mode, display_profile=display_profile)

    monkeypatch.setattr(session, "resolve_acquisition", resolver)

    def acquire(request):
        path = Path(request["destination"])
        _write_epub(path)
        return {
            "ok": True, "operation": "download",
            "artifact": {"path": str(path), "format": "epub", "drm": "none",
                         "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        }

    monkeypatch.setattr(service, "execute_acquisition_request", acquire)
    try:
        first = service.sync_kobo_backup()
        assert delegated_calls == ["bad0"]
        assert first["items_blocked_by_scope"] == 4
        assert service.kobo_backup_diagnostic()["items_blocked_by_scope"] == 4
        assert first["attempts_saved"] >= 3
        assert first["downloaded"] == 1
        assert first["errors"] == 0
        for i in range(4):
            row = service.storage.backup_get("kobo", "vault-1", f"bad{i}")
            assert row["status"] == "attention_required"
            assert row["attention_code"] == "missing_delegation_scope"

        # A second invocation on the same session must not probe forbidden
        # delegation again; a reconnect is the explicit cache-reset event.
        second = service.sync_kobo_backup()
        assert delegated_calls == ["bad0"]
        assert second["items_blocked_by_scope"] == 4
    finally:
        service.close()
