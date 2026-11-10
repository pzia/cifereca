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
from cifereca.providers import ProviderConfigurationError

import pytest


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



@pytest.mark.parametrize("manifest", [
    [], [{"product_id": "book-1", "title": "private title"}],
    [{"product_id": "bad", "owned": "false"}],
    [{"product_id": "bad", "downloadable": False}],
    [{"product_id": "dup"}, {"product_id": "dup"}],
    [{"title": "no product id"}],
])
@pytest.mark.parametrize("prior_status", [None, "acquiring"])
def test_retired_w2_cannot_claim_progress_or_success(
    tmp_path: Path, monkeypatch, manifest, prior_status,
):
    """No 0.7 GET, checkpoint, diagnostic success or progress event."""
    service, session = _service(tmp_path, manifest=manifest)
    try:
        if prior_status:
            service.storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault-1",
                "identity_id": "default", "account_id": "account",
                "item_id": "historic", "title": "Historic",
                "status": prior_status,
            })
        before = service.storage.backup_list("kobo")
        clock = service.storage.metadata_get("kobo_backup_last_success_at")
        calls = []
        monkeypatch.setattr(session, "backup_manifest", lambda: pytest.fail(
            "retired W2 must not GET supplier manifest"
        ))
        monkeypatch.setattr(service, "execute_acquisition_request",
                            lambda *_a, **_kw: pytest.fail("no provider GET"))
        monkeypatch.setattr(service.storage, "backup_put",
                            lambda *_a, **_kw: pytest.fail("no backup writer"))
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_w2_retired_use_canonical_provider",
        ):
            service.sync_kobo_backup(progress=calls.append)
        assert calls == []
        assert service.storage.backup_list("kobo") == before
        assert service.storage.metadata_get("kobo_backup_last_success_at") == clock
        assert service.storage.metadata_get("kobo_backup_last_attempt_at") is None
    finally:
        service.close()
