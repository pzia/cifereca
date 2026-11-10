"""Shared Kobo synthetic fixtures; retired W2 execution tests removed."""
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
    supports_acquisition_delegation = True  # Synthetic verified token scope
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

