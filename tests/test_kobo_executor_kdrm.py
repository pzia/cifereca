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
    supports_acquisition_delegation = True  # Synthetic verified token scope
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


@pytest.mark.parametrize("delivery_source", ["content_access_book", "library_sync"])
@pytest.mark.parametrize("capabilities", [
    (False, False, False), (True, False, False),
    (True, True, False), (True, True, True),
])
def test_retired_d3_never_builds_kdrm_get_recipe_without_canonical_abi(
    tmp_path: Path, monkeypatch, delivery_source, capabilities,
):
    service, session = _service(tmp_path, delivery_source=delivery_source)
    service.kobo_adept_state = lambda **_kw: _state(
        pinned=capabilities[0], separate_keys=capabilities[1],
        kdrm=capabilities[2],
    )
    requests = []
    def forbidden(*_a, **_kw):
        pytest.fail("no legacy Kobo descriptor or runtime KDRM GET")
    monkeypatch.setattr(session, "resolve_acquisition", forbidden)
    monkeypatch.setattr(service, "execute_acquisition_request",
                        lambda x: requests.append(x) or forbidden())
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_legacy_direct_get_retired"):
            service.acquire_direct_item(_item())
        assert requests == []
        assert session.acquisition_modes == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()
