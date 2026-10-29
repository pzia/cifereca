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


def _write_epub(path: Path, marker: bytes = b"book") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", marker)


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


class KoboSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False

    def __init__(
        self,
        *args,
        manifest=None,
        cover_value=None,
        supports_acsm=False,
        acsm_requires_auth=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest or [])
        self._cover_value = cover_value
        self.supports_acquisition_acsm = supports_acsm
        self.acsm_requires_auth = acsm_requires_auth
        self.acquisition_modes: list[str] = []

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

    def cover_url(self, item):
        if self._cover_value is not None:
            return self._cover_value
        return item.get("cover_url")

    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
        include_acsm=None,
    ):
        self.acquisition_modes.append(mode)
        assert include_acsm is True
        headers = (
            {"Authorization": "Bearer delegated-secret"}
            if mode == "delegated"
            else {}
        )
        return {
            "provider": "kobo",
            "schema_version": "1.4",
            "acquisition": {
                "product_id": product_id,
                "revision_id": revision_id,
                "mode": mode,
                "validity": {},
                "candidates": [{
                    "kind": "acsm",
                    "transport": "https",
                    "method": "GET",
                    "url": "https://cdn.example/book.acsm",
                    "headers": headers,
                    "media_format": "acsm",
                    "source_format": "ACSM",
                    "handling": "adobe_adept",
                    "drm": {
                        "family": "adobe",
                        "raw_type": "AdobeDrm",
                        "protected": True,
                    },
                    "kobo": {
                        "source": "library_download_link",
                        "requires_kobo_authorization": self.acsm_requires_auth,
                    },
                }],
            },
        }


def _service(
    tmp_path: Path,
    *,
    manifest=None,
    cover_value=None,
    supports_acsm=False,
    acsm_requires_auth=False,
):
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
    session = KoboSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=manifest,
        cover_value=cover_value,
        supports_acsm=supports_acsm,
        acsm_requires_auth=acsm_requires_auth,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.catalog_scan = lambda *args, **kwargs: {"scanned": 0}
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service, session


def _ready_state(*, acsm=True):
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": acsm,
        "kobo_delegated_available": True,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "strict_json_stdout": True,
        "software_version": "test",
        "error": None,
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


def _manifest_row(*, archived=False, downloadable=True, cover_url=None):
    row = {
        "product_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "authors": ["Author"],
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": downloadable,
        "archived": archived,
    }
    if cover_url is not None:
        row["cover_url"] = cover_url
    return row


def test_acsm_requiring_kobo_authorization_is_re_resolved_delegated(tmp_path: Path, monkeypatch):
    service, session = _service(
        tmp_path,
        supports_acsm=True,
        acsm_requires_auth=True,
    )
    service.kobo_adept_state = lambda **kwargs: _ready_state(acsm=True)
    service.resolve_adept_profile = lambda **kwargs: ("hb", "identity")

    requests: list[dict] = []
    monkeypatch.setattr(
        service,
        "execute_acquisition_request",
        lambda request: requests.append(request) or _artifact_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes[:2] == ["descriptor", "delegated"]
        assert len(requests) == 1
        request = requests[0]
        assert request["source"]["type"] == "acsm"
        assert request["source"]["headers"] == {"Authorization": "Bearer delegated-secret"}
        assert request["profile"] == "hb"
        assert result.metadata["acquisition_mode"] == "acsm-delegated"
    finally:
        service.close()


def test_archived_manifest_is_skipped_without_calling_adept(tmp_path: Path, monkeypatch):
    service, _session = _service(
        tmp_path,
        manifest=[_manifest_row(archived=True, downloadable=False)],
    )
    service.kobo_adept_state = lambda **kwargs: _ready_state(acsm=False)
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("archived item must not call ADEPT")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["attention_required"] == 0
        assert summary["skipped_archived"] == 1
        assert summary["errors"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "skipped_archived"
        assert row["attention_code"] is None
        assert row["error"] is None
    finally:
        service.close()


def test_archived_remote_keeps_existing_complete_local_artifact(tmp_path: Path, monkeypatch):
    service, _session = _service(
        tmp_path,
        manifest=[_manifest_row(archived=True, downloadable=False)],
    )
    service.kobo_adept_state = lambda **kwargs: _ready_state(acsm=False)

    artifact = service._kobo_backup_target_dir("default") / "Author - Book One.epub"
    _write_epub(artifact)
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "account_id": "account",
        "identity_id": "default",
        "item_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "author": "Author",
        "format": "EPUB",
        "status": "complete",
        "local_path": str(artifact),
        "size": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    })
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("existing archived local item must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["skipped_archived"] == 1
        assert summary["attention_required"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "complete"
        assert Path(str(row["local_path"])).is_file()
    finally:
        service.close()


def test_backup_refreshes_cover_metadata_without_redownload(tmp_path: Path, monkeypatch):
    service, _session = _service(
        tmp_path,
        manifest=[_manifest_row(cover_url="https://cdn.example/new-cover.jpg")],
    )
    service.kobo_adept_state = lambda **kwargs: _ready_state(acsm=False)

    artifact = service._kobo_backup_target_dir("default") / "Book One.epub"
    _write_epub(artifact)
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "account_id": "account",
        "identity_id": "default",
        "item_id": "book-1",
        "revision_id": "rev-1",
        "title": "Old title",
        "author": "Old author",
        "format": "EPUB",
        "status": "complete",
        "local_path": str(artifact),
        "size": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "cover_url": None,
    })
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("metadata refresh must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["unchanged"] == 1
        assert summary["downloaded"] == 0
        assert summary["metadata_refreshed"] == 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["cover_url"] == "https://cdn.example/new-cover.jpg"
        assert row["title"] == "Book One"
        assert row["author"] == "Author"
        assert row["status"] == "complete"
        assert Path(str(row["local_path"])) == artifact
    finally:
        service.close()


def test_missing_vault_cover_does_not_erase_existing_cover(tmp_path: Path, monkeypatch):
    service, _session = _service(
        tmp_path,
        manifest=[_manifest_row()],
        cover_value="",
    )
    service.kobo_adept_state = lambda **kwargs: _ready_state(acsm=False)

    artifact = service._kobo_backup_target_dir("default") / "Book One.epub"
    _write_epub(artifact)
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "account_id": "account",
        "identity_id": "default",
        "item_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "author": "Author",
        "format": "EPUB",
        "status": "complete",
        "local_path": str(artifact),
        "size": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "cover_url": "https://cdn.example/existing.jpg",
    })
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unchanged local item must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["unchanged"] == 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["cover_url"] == "https://cdn.example/existing.jpg"
    finally:
        service.close()
