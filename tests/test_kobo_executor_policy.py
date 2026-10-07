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


def test_configured_adept_profile_prefers_acsm_over_plain_and_kdrm(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, payload_factory=_mixed_payload, supports_acsm=True)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=True, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("hb", "identity")

    requests: list[dict] = []
    monkeypatch.setattr(service, "execute_acquisition_request", lambda request: requests.append(request) or _artifact_result(request))
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor"]
        assert session.include_acsm_values == [True]
        assert len(requests) == 1
        request = requests[0]
        assert request["source"]["type"] == "acsm"
        assert request["source"]["url"] == "https://cdn.example/book.acsm"
        assert request["profile"] == "hb"
        assert "format" not in request
        assert request["metadata"]["vault_candidate_source"] == "library_download_link"
        assert result.metadata["acquisition_mode"] == "acsm-descriptor"
    finally:
        service.close()


def test_without_adept_profile_signednodrm_wins_before_kdrm(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, payload_factory=_mixed_payload, supports_acsm=True)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=True, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")

    requests: list[dict] = []
    monkeypatch.setattr(service, "execute_acquisition_request", lambda request: requests.append(request) or _artifact_result(request))
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor"]
        assert len(requests) == 1
        request = requests[0]
        assert request["source"]["type"] == "http"
        assert request["source"]["url"] == "https://cdn.example/plain.epub"
        assert request["drm"] == "SignedNoDrm"
        assert result.metadata["acquisition_mode"] == "descriptor-http"
    finally:
        service.close()


def test_kepub_signednodrm_http_plain_never_uses_delegated_kobo(tmp_path: Path, monkeypatch):
    def payload(product_id, revision_id, mode):
        if mode != "descriptor":
            raise AssertionError("http_plain must not be re-routed through delegated Kobo")
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
                        url="https://getbook.kobo.com/book.epub",
                        media_format="kepub",
                        source_format="KEPUB",
                        handling="http_plain",
                        drm="SignedNoDrm",
                    )
                ],
            },
        }

    service, session = _service(tmp_path, payload_factory=payload)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=False, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")

    requests: list[dict] = []
    monkeypatch.setattr(service, "execute_acquisition_request", lambda request: requests.append(request) or _artifact_result(request))
    try:
        result = service.acquire_direct_item(_item())

        assert result.path and result.path.is_file()
        assert session.acquisition_modes == ["descriptor"]
        request = requests[0]
        assert request["source"]["type"] == "http"
        assert request["format"] == "epub"
        assert request["drm"] == "SignedNoDrm"
        assert result.metadata["acquisition_mode"] == "descriptor-http"
    finally:
        service.close()


def test_http_plain_forwards_candidate_headers_with_url(tmp_path: Path, monkeypatch):
    def payload(product_id, revision_id, mode):
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
                        url="https://storedownloads.kobo.com/book",
                        headers={"x-amz-request-payer": "requester"},
                        media_format="epub",
                        source_format="EPUB3",
                        handling="http_plain",
                        drm="SignedNoDrm",
                    )
                ],
            },
        }

    service, _session = _service(tmp_path, payload_factory=payload)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=False, delegated=False)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")

    requests: list[dict] = []
    monkeypatch.setattr(service, "execute_acquisition_request", lambda request: requests.append(request) or _artifact_result(request))
    try:
        service.acquire_direct_item(_item())

        request = requests[0]
        assert request["source"]["url"] == "https://storedownloads.kobo.com/book"
        assert request["source"]["headers"] == {"x-amz-request-payer": "requester"}
    finally:
        service.close()
