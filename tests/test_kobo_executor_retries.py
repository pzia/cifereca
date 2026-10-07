from __future__ import annotations

import hashlib
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


def _write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>Test</body></html>")


class FakeKoboSession(ProviderSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_version = "2.3"
        self.display_profile = "Android"
        self.supports_acquisition_display_profile = True
        self.supports_acquisition_acsm = False
        self.acquisition_modes: list[str] = []
        self.acquisition_profiles: list[str | None] = []

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
        display_profile=None,
    ):
        self.acquisition_modes.append(mode)
        self.acquisition_profiles.append(display_profile)
        headers = {"Authorization": "Bearer ephemeral"} if mode == "delegated" else {}
        return {
            "provider": "kobo",
            "schema_version": "1.4",
            "acquisition": {
                "product_id": product_id,
                "revision_id": revision_id,
                "mode": mode,
                "validity": {"expires_at": None},
                "candidates": [{
                    "transport": "https",
                    "method": "GET",
                    "url": "https://cdn.example/book.epub",
                    "headers": headers,
                    "drm_type": "SignedNoDrm",
                    "format": "epub",
                    "protected": False,
                }],
            },
        }


class ProfileFallbackSession(FakeKoboSession):
    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
        display_profile=None,
    ):
        self.acquisition_modes.append(mode)
        self.acquisition_profiles.append(display_profile)
        media_format = "audio" if display_profile == "Android" else "epub"
        return {
            "provider": "kobo",
            "schema_version": "1.4",
            "acquisition": {
                "product_id": product_id,
                "revision_id": revision_id,
                "mode": mode,
                "validity": {"expires_at": None},
                "candidates": [{
                    "transport": "https",
                    "method": "GET",
                    "url": "https://cdn.example/book",
                    "headers": {},
                    "drm_type": "SignedNoDrm",
                    "format": media_format,
                    "protected": False,
                }],
            },
        }


def _service(tmp_path: Path, session_type=FakeKoboSession):
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
    session = session_type(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
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
        "strict_json_stdout": True,
    }
    service.resolve_adept_profile = lambda **kwargs: ("", "test")
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


def _success_result(request: dict) -> dict:
    target = Path(request["destination"])
    _write_epub(target)
    return {
        "ok": True,
        "operation": "download",
        "download_id": "d1",
        "artifact": {
            "path": str(target),
            "format": "epub",
            "drm": "none",
            "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    }


def test_source_auth_expired_re_resolves_delegated_and_forwards_ephemeral_header(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path)
    calls: list[dict] = []

    def execute(request):
        calls.append(request)
        if len(calls) == 1:
            raise KoboAdeptError("expired", code="source_auth_expired", exit_code=4)
        return _success_result(request)

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor", "delegated"]
        assert calls[0]["source"]["headers"] == {}
        assert calls[1]["source"]["headers"] == {"Authorization": "Bearer ephemeral"}
    finally:
        service.close()


def test_unusable_android_candidate_falls_back_to_desktop_profile(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, ProfileFallbackSession)
    calls: list[dict] = []
    monkeypatch.setattr(
        service,
        "execute_acquisition_request",
        lambda request: calls.append(request) or _success_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor", "descriptor"]
        assert session.acquisition_profiles == ["Android", "Desktop"]
        assert len(calls) == 1
        assert calls[0]["metadata"]["vault_display_profile"] == "Desktop"
    finally:
        service.close()
