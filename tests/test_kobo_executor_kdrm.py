from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.providers import (
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderSession,
)
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>Kobo KDRM test</body></html>")


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
        "source": {"type": "kobo", "kobo": {"kdrm_removed": True}},
    }


def _kdrm_candidate(*, source: str, media_format: str = "kepub", source_format: str = "KEPUB") -> dict:
    return {
        "kind": "http",
        "transport": "https",
        "method": "GET",
        "url": "https://storedownloads.kobo.com/book-payload",
        "headers": {"x-amz-request-payer": "requester"},
        "media_format": media_format,
        "source_format": source_format,
        "handling": "kobo_kdrm",
        "drm": {
            "family": "kobo_kdrm",
            "raw_type": "KDRM",
            "protected": True,
        },
        "kobo": {
            "source": source,
            "display_profile": "Android",
            "platform": "Generic",
        },
        "drm_type": "KDRM",
        "format": source_format,
        "protected": True,
    }


class KdrmSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, delivery_source="content_access_book", **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery_source = delivery_source
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

    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
    ):
        self.acquisition_modes.append(mode)
        media_format = "unknown" if self.delivery_source == "library_sync" else "kepub"
        source_format = "FLEPUB" if self.delivery_source == "library_sync" else "KEPUB"
        acquisition = {
            "product_id": product_id,
            "revision_id": revision_id,
            "mode": mode,
            "validity": {},
            "candidates": [
                _kdrm_candidate(
                    source=self.delivery_source,
                    media_format=media_format,
                    source_format=source_format,
                )
            ],
        }
        if mode == "delegated":
            acquisition["delegation"] = {
                "provider": "kobo",
                "content_access_request": {
                    "url": f"https://storeapi.kobo.com/v1/products/books/{product_id}/access",
                    "method": "GET",
                    "headers": {"Authorization": "Bearer delegated-secret"},
                    "query": {"DisplayProfile": "Android"},
                },
                "session_context": {
                    "device_id": "device-123",
                    "user_id": "user-456",
                    "serial_number": "serial-789",
                },
            }
        return {
            "provider": "kobo",
            "schema_version": "1.4",
            "acquisition": acquisition,
        }


def _service(tmp_path: Path, *, delivery_source="content_access_book"):
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
    session = KdrmSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        delivery_source=delivery_source,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.resolve_adept_profile = lambda **kwargs: ("", "test")
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service, session


def _state(
    *,
    delegated=True,
    pinned=False,
    separate_keys=False,
    kdrm=True,
) -> dict:
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": False,
        "kobo_delegated_available": delegated,
        "kobo_pinned_delivery_candidate": pinned,
        "kobo_separate_content_keys_replay": separate_keys,
        "kobo_kdrm_available": kdrm,
        "kobo_formats": ["epub", "kepub", "pdf"],
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


def test_delegated_kdrm_builds_content_access_recipe_with_context(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path)
    service.kobo_adept_state = lambda **kwargs: _state()
    requests: list[dict] = []
    monkeypatch.setattr(
        service,
        "execute_acquisition_request",
        lambda request: requests.append(request) or _artifact_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor", "delegated"]
        request = requests[0]
        assert request["schema_version"] == 2
        assert request["source"]["type"] == "kobo"
        assert request["source"]["provider"] == "kobo"
        assert request["source"]["request"]["url"].endswith("/products/books/book-1/access")
        assert request["source"]["request"]["headers"] == {"Authorization": "Bearer delegated-secret"}
        assert request["source"]["request"]["query"] == {"DisplayProfile": "Android"}
        assert request["source"]["selection"] == {"drm_type": "KDRM", "url_format": "KEPUB"}
        assert request["source"]["kdrm"] == {"device_id": "device-123", "user_id": "user-456"}
        assert "ContentKeys" not in str(request)
        assert result.metadata["acquisition_mode"] == "delegated-kobo"
    finally:
        service.close()


def test_library_sync_kdrm_builds_pinned_delivery_candidate_recipe(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, delivery_source="library_sync")
    service.kobo_adept_state = lambda **kwargs: _state(
        pinned=True,
        separate_keys=True,
        kdrm=True,
    )
    requests: list[dict] = []
    monkeypatch.setattr(
        service,
        "execute_acquisition_request",
        lambda request: requests.append(request) or _artifact_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor", "delegated"]
        request = requests[0]
        source = request["source"]
        assert source["type"] == "kobo"
        assert source["provider"] == "kobo-vault"
        assert source["kdrm"] == {"device_id": "device-123", "user_id": "user-456"}
        assert "selection" not in source
        assert source["candidate"] == {
            "url": "https://storedownloads.kobo.com/book-payload",
            "headers": {"x-amz-request-payer": "requester"},
            "drm_type": "KDRM",
            "media_format": "unknown",
            "source_format": "FLEPUB",
            "delivery_source": "library_sync",
            "platform": "Generic",
        }
        assert request["format"] == "epub"
        assert result.metadata["acquisition_mode"] == "delegated-kobo"
    finally:
        service.close()


@pytest.mark.parametrize(
    ("pinned", "separate_keys", "kdrm", "missing"),
    [
        (False, True, True, "pinned_delivery_candidate"),
        (True, False, True, "separate_content_keys_replay"),
        (True, True, False, "KDRM"),
    ],
)
def test_library_sync_kdrm_requires_explicit_runtime_capabilities(
    tmp_path: Path,
    monkeypatch,
    pinned: bool,
    separate_keys: bool,
    kdrm: bool,
    missing: str,
):
    service, session = _service(tmp_path, delivery_source="library_sync")
    service.kobo_adept_state = lambda **kwargs: _state(
        pinned=pinned,
        separate_keys=separate_keys,
        kdrm=kdrm,
    )
    called = {"runtime": False}

    def execute(_request):
        called["runtime"] = True
        raise AssertionError("runtime must not run when a pinned KDRM capability is missing")

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    try:
        with pytest.raises(ProviderConfigurationError, match=missing):
            service.acquire_direct_item(_item())

        assert session.acquisition_modes == ["descriptor", "delegated"]
        assert called["runtime"] is False
    finally:
        service.close()


def test_kdrm_without_delegated_capability_never_falls_back_to_http(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path)
    service.kobo_adept_state = lambda **kwargs: _state(
        delegated=False,
        pinned=False,
        separate_keys=False,
        kdrm=False,
    )
    called = {"runtime": False}

    def execute(_request):
        called["runtime"] = True
        raise AssertionError("KDRM must never be flattened to direct HTTP")

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    try:
        with pytest.raises(ProviderConfigurationError, match="HTTP direct"):
            service.acquire_direct_item(_item())

        assert session.acquisition_modes == ["descriptor", "delegated"]
        assert called["runtime"] is False
    finally:
        service.close()
