from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.kobo.acquisition import KoboOfferAdapter
from cifereca.kobo.errors import KoboAdeptError
from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderConfigurationError
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


class RotatingURLSession(FakeKoboSession):
    refreshed_candidate: dict | None = None

    def resolve_acquisition(
        self,
        product_id,
        *,
        revision_id=None,
        mode="descriptor",
        display_profile=None,
    ):
        payload = super().resolve_acquisition(
            product_id, revision_id=revision_id, mode=mode, display_profile=display_profile,
        )
        if mode == "delegated":
            candidate = payload["acquisition"]["candidates"][0]
            candidate["url"] = "https://cdn.example/renewed-book.epub"
            self.refreshed_candidate = dict(candidate)
        return payload


def test_kobo_expired_url_retry_persists_new_candidate_and_attempt(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, RotatingURLSession)
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
        assert len(calls) == 2
        assert calls[1]["source"]["url"] == "https://cdn.example/renewed-book.epub"

        assert session.refreshed_candidate is not None
        expected = KoboOfferAdapter.normalize_candidate(session.refreshed_candidate)
        rows = service.storage.acquisitions(provider_id="kobo")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "complete"
        assert row["attempt_count"] == 2
        assert row["candidate_id"] == expected.candidate_id
        assert row["strategy"] == "http_plain"
        assert row["executor"] == "kobo-adept"
        assert row["metadata"]["candidate_shape"] == expected.safe_shape()
        assert row["metadata"]["acquisition_mode"] == "delegated-http"
    finally:
        service.close()


def test_kobo_runtime_failure_is_retryable_in_ledger(tmp_path: Path, monkeypatch):
    service, _session = _service(tmp_path)

    def broken(_request):
        raise KoboAdeptError("download failed", code="source_unavailable", retryable=True)

    monkeypatch.setattr(service, "execute_acquisition_request", broken)
    try:
        with pytest.raises(KoboAdeptError, match="download failed"):
            service.acquire_direct_item(_item())

        rows = service.storage.acquisitions(provider_id="kobo")
        assert len(rows) == 1
        assert rows[0]["status"] == "retryable_error"
        assert rows[0]["error_code"] == "source_unavailable"
        assert rows[0]["attempt_count"] == 1
        assert "Bearer" not in (rows[0]["error_message"] or "")
    finally:
        service.close()


def test_kobo_second_attempt_failure_never_leaves_acquiring(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path)
    calls = []

    def execute(request):
        calls.append(request)
        if len(calls) == 1:
            raise KoboAdeptError("expired", code="source_auth_expired", exit_code=4)
        raise KoboAdeptError("runtime rejected", code="not_authorized", retryable=False)

    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    try:
        with pytest.raises(KoboAdeptError, match="runtime rejected"):
            service.acquire_direct_item(_item())

        rows = service.storage.acquisitions(provider_id="kobo")
        assert len(rows) == 1
        assert rows[0]["status"] == "attention_required"
        assert rows[0]["error_code"] == "not_authorized"
        assert rows[0]["attempt_count"] == 2
        assert rows[0]["metadata"]["acquisition_mode"] == "delegated-http"
        assert session.acquisition_modes == ["descriptor", "delegated"]
    finally:
        service.close()


def test_kobo_configuration_failure_is_attention_required(tmp_path: Path, monkeypatch):
    service, _session = _service(tmp_path)

    def misconfigured(_request):
        raise ProviderConfigurationError("bad config")

    monkeypatch.setattr(service, "execute_acquisition_request", misconfigured)
    try:
        with pytest.raises(ProviderConfigurationError, match="bad config"):
            service.acquire_direct_item(_item())
        row = service.storage.acquisitions(provider_id="kobo")[0]
        assert row["status"] == "attention_required"
        assert row["error_code"] == "kobo_acquisition_failed"
        assert row["attempt_count"] == 1
    finally:
        service.close()


def test_kobo_preflight_failure_does_not_create_false_ledger_entry(tmp_path: Path):
    service, _session = _service(tmp_path)
    service.kobo_adept_state = lambda **_kwargs: {"available": False}
    try:
        with pytest.raises(ProviderConfigurationError, match="n'est pas prêt"):
            service.acquire_direct_item(_item())
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()

def test_delegation_scope_denied_after_expired_url_is_not_retried(tmp_path: Path, monkeypatch):
    """A real plugin 403 on URL refresh stays a scoped, nonretryable error."""
    import importlib.util
    from dataclasses import replace

    from cifereca.kobo.vault import KoboVaultError as CoreKoboVaultError

    plugin_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-kobo/cifereca_kobo_vault.py"
    # Keep direct-file provider imports independent from the global test path.
    monkeypatch.syspath_prepend(str(plugin_path.parent))
    spec = importlib.util.spec_from_file_location("test_real_vault_refresh_scope", plugin_path)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    assert plugin.KoboVaultError is not CoreKoboVaultError

    service, session = _service(tmp_path)
    original_resolver = session.resolve_acquisition
    delegated_requests: list[str] = []
    runtime_requests: list[dict] = []

    def resolve(product_id, *, revision_id=None, mode="descriptor", display_profile=None):
        if mode == "delegated":
            delegated_requests.append(product_id)
            raise plugin.KoboVaultError(
                "Kobo-Vault : forbidden", code="forbidden", status_code=403,
            )
        return original_resolver(
            product_id, revision_id=revision_id, mode=mode,
            display_profile=display_profile,
        )

    def expired(request):
        runtime_requests.append(request)
        raise KoboAdeptError("expired", code="source_auth_expired", exit_code=4)

    monkeypatch.setattr(session, "resolve_acquisition", resolve)
    monkeypatch.setattr(service, "execute_acquisition_request", expired)
    try:
        first = _item()
        second = replace(first, id="kobo:vault-1:book-2", record_id="book-2")
        for item in (first, second):
            with pytest.raises(CoreKoboVaultError) as caught:
                service.acquire_direct_item(item)
            assert caught.value.status_code == 403
            assert caught.value.code == "missing_delegation_scope"

        # The first item probes the missing permission. Subsequent items
        # still inspect descriptors but send no other forbidden delegated GET.
        assert delegated_requests == ["book-1"]
        assert getattr(session, "_cifereca_delegation_attempts_saved", 0) >= 1
        assert len(runtime_requests) == 2
        rows = service.storage.acquisitions(provider_id="kobo")
        assert len(rows) == 2
        assert all(row["status"] == "attention_required" for row in rows)
        assert all(row["error_code"] == "missing_delegation_scope" for row in rows)
    finally:
        service.close()


@pytest.mark.parametrize("status,scope_code", [
    (403, "missing_delegation_scope"),
    (401, "unauthorized"),
])
def test_provider_permission_denial_cannot_override_nonretryable_ledger(
    tmp_path: Path, monkeypatch, status, scope_code,
):
    """The plugin's retryable hint cannot restart a denied HTTP permission."""
    import importlib.util

    plugin_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-kobo/cifereca_kobo_vault.py"
    # Keep direct-file provider imports independent from the global test path.
    monkeypatch.syspath_prepend(str(plugin_path.parent))
    spec = importlib.util.spec_from_file_location(
        "test_vault_nonretryable_permission", plugin_path,
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    service, _session = _service(tmp_path)
    rejected = plugin.KoboVaultError(
        "provider denied authorization", code=scope_code, status_code=status,
    )
    rejected.retryable = True  # hostile/inconsistent upstream retry hint

    def deny(_request):
        raise rejected

    monkeypatch.setattr(service, "execute_acquisition_request", deny)
    try:
        with pytest.raises(plugin.KoboVaultError) as caught:
            service.acquire_direct_item(_item())
        assert caught.value.status_code == status
        rows = service.storage.acquisitions(provider_id="kobo")
        assert len(rows) == 1
        assert rows[0]["status"] == "attention_required"
        assert rows[0]["error_code"] == scope_code
        assert rows[0]["attempt_count"] == 1
    finally:
        service.close()
