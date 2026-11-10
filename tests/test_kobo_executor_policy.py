"""Synthetic provider fixtures retained for negative D3 contract tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
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


def _artifact_result(request: dict) -> dict:
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


def _candidate(
    *,
    url: str,
    media_format: str,
    source_format: str,
    handling: str,
    drm: str,
    headers: dict | None = None,
    source: str = "content_access_book",
    requires_auth: bool = False,
    kind: str = "http",
):
    family = "none" if drm == "SignedNoDrm" else "adobe" if drm == "AdobeDrm" else "kobo"
    return {
        "kind": kind,
        "transport": "https",
        "method": "GET",
        "url": url,
        "headers": dict(headers or {}),
        "media_format": media_format,
        "source_format": source_format,
        "handling": handling,
        "drm": {
            "family": family,
            "raw_type": drm,
            "protected": drm != "SignedNoDrm",
        },
        "kobo": {
            "source": source,
            "requires_kobo_authorization": requires_auth,
        },
    }


class PolicySession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_delegation = True  # Synthetic verified token scope
    supports_acquisition_display_profile = False

    def __init__(self, *args, payload_factory, supports_acsm=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.payload_factory = payload_factory
        self.supports_acquisition_acsm = supports_acsm
        self.acquisition_modes: list[str] = []
        self.include_acsm_values: list[bool | None] = []

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

    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
        include_acsm=None,
    ):
        self.acquisition_modes.append(mode)
        self.include_acsm_values.append(include_acsm)
        return self.payload_factory(product_id, revision_id, mode)


def _service(tmp_path: Path, *, payload_factory, supports_acsm=False):
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
    session = PolicySession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        payload_factory=payload_factory,
        supports_acsm=supports_acsm,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service, session


def _state(*, acsm: bool, delegated: bool) -> dict:
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": acsm,
        "kobo_delegated_available": delegated,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": delegated,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "strict_json_stdout": True,
    }


def _item() -> SearchResult:
    return SearchResult(
        id="kobo:vault-1:book-1",
        record_id="book-1",
        docbase="KOBO",
        medium="digital",
        title="Book One",
        author="Author",
        type="Livre numérique Kobo",
        format="EPUB",
        publisher=None,
        published_at=None,
        description=None,
        thumbnail_url=None,
        friendly_url=None,
        scenario="KOBO",
        source_provider="kobo",
        source_credential="vault-1",
        content_type="ebook",
        source_collection="library",
        owned=True,
        downloadable=True,
        revision_id="rev-1",
        metadata={},
    )


def _mixed_payload(product_id, revision_id, mode):
    return {
        "provider": "kobo",
        "schema_version": "1.4",
        "acquisition": {
            "product_id": product_id,
            "revision_id": revision_id,
            "mode": mode,
            "validity": {},
            "candidates": [
                _candidate(
                    kind="acsm",
                    url="https://cdn.example/book.acsm",
                    media_format="acsm",
                    source_format="ACSM",
                    handling="adobe_adept",
                    drm="AdobeDrm",
                    source="library_download_link",
                ),
                _candidate(
                    url="https://cdn.example/plain.epub",
                    media_format="epub",
                    source_format="EPUB",
                    handling="http_plain",
                    drm="SignedNoDrm",
                ),
                _candidate(
                    url="https://cdn.example/kdrm",
                    media_format="kepub",
                    source_format="KEPUB",
                    handling="kobo_kdrm",
                    drm="KDRM",
                ),
            ],
        },
    }

