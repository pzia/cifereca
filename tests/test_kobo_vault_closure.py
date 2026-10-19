from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.kobo.errors import KoboAdeptError
from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


class KoboSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, source_format="KEPUB", manifest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_format = source_format
        self._manifest = list(manifest or [])

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

    def resolve_acquisition(self, product_id, *, revision_id=None, mode="descriptor"):
        return {
            "provider": "kobo",
            "schema_version": "1.4",
            "acquisition": {
                "product_id": product_id,
                "revision_id": revision_id,
                "mode": mode,
                "validity": {},
                "candidates": [{
                    "kind": "http",
                    "transport": "https",
                    "method": "GET",
                    "url": "https://cdn.example/book",
                    "headers": {},
                    "media_format": self.source_format.lower(),
                    "source_format": self.source_format,
                    "handling": "http_plain",
                    "drm": {
                        "family": "none",
                        "raw_type": "SignedNoDrm",
                        "protected": False,
                    },
                    "kobo": {"source": "content_access_book"},
                }],
            },
        }

    def backup_manifest(self):
        return list(self._manifest)

    def cover_url(self, _raw):
        return None

    def normalize_item(self, raw, default_collection="library"):
        return SearchResult(
            id=f"kobo:{self.credential_id}:{raw['product_id']}",
            record_id=str(raw["product_id"]),
            docbase="KOBO",
            medium="digital",
            title=str(raw.get("title") or raw["product_id"]),
            author="Author",
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


def _service(tmp_path: Path, *, source_format="KEPUB", manifest=None):
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
    session = KoboSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        source_format=source_format,
        manifest=manifest,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.resolve_adept_profile = lambda **kwargs: ("", "none")
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service, session


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


def _ready_state():
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "strict_json_stdout": True,
        "software_version": "test",
        "error": None,
    }


@pytest.mark.parametrize("source_format", ["KEPUB", "EPUB3"])
def test_kobo_formats_are_normalized_to_epub_and_preserve_original_format(
    tmp_path: Path,
    monkeypatch,
    source_format: str,
):
    service, _session = _service(tmp_path, source_format=source_format)
    service.kobo_adept_state = lambda **kwargs: _ready_state()

    requests: list[dict] = []

    def execute(request):
        requests.append(request)
        destination = Path(request["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr("content.xhtml", "<html><body>normalized</body></html>")
        return {
            "ok": True,
            "artifact": {
                "path": str(destination),
                "format": "epub",
                "drm": "none",
            },
        }

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    try:
        service.acquire_direct_item(_item())

        assert len(requests) == 1
        request = requests[0]
        assert request["format"] == "epub"
        assert request["metadata"]["original_format"] == source_format.lower()
    finally:
        service.close()


def test_real_adept_failure_is_counted_only_in_adept_errors(tmp_path: Path, monkeypatch):
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
    service.catalog_scan = lambda *args, **kwargs: {"scanned": 0}
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.kobo_adept_state = lambda **kwargs: _ready_state()

    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            KoboAdeptError(
                "Kobo-Adept : EPUB invalide",
                code="invalid_epub",
                retryable=False,
                exit_code=3,
            )
        ),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["errors"] == 1
        assert summary["adept_errors"] == 1
        assert summary["vault_errors"] == 0
        assert summary["integration_errors"] == 0
        assert summary["aborted"] is False
        assert summary["error_summary"] == [{
            "phase": "adept",
            "code": "invalid_epub",
            "status_code": None,
            "message": "Kobo-Adept : EPUB invalide",
            "count": 1,
        }]

        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "error"
        # Durable receipts are public diagnostic data; the typed in-process
        # summary above retains the ADEPT decision detail.
        assert row["error"] == "Sauvegarde Kobo impossible"
        assert "EPUB invalide" not in row["error"]
    finally:
        service.close()
