from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service._adept_return_capability = lambda: True
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    return service


def _acquisition(service: LibraryService, tmp_path: Path) -> str:
    path = tmp_path / "EBooks" / "hugues" / "paris" / "Livre.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"existing-local-artifact")
    return service.storage.acquisition_put({
        "acquisition_id": "acq-1",
        "identity_id": "default",
        "provider_id": "paris",
        "credential_id": "reader",
        "account_id": "account",
        "item_id": "book",
        "title": "Livre",
        "status": "complete",
        "executor": "kobo-adept",
        "metadata": {
            "path": str(path),
            "adept_binding": {
                "adept_profile_id": "stable-profile",
                "adept_profile_name": "hb",
                "adept_loan_id": "registry-id",
                "loan_state": "active",
                "token_present": True,
                "return_available": True,
            },
        },
    })


def _loan(*, returned: bool, token_present: bool, expired: bool = False):
    return {
        "id": "registry-id",
        "returned": returned,
        "expired": expired,
        "token_present": token_present,
    }


@pytest.mark.parametrize("acquisition_id", ["acq-1", "missing", "", "PRIVATE-IDENTIFIER"])
def test_legacy_adept_return_is_refused_before_any_inventory_or_mutation(
    tmp_path: Path, acquisition_id: str,
):
    service = _service(tmp_path)
    _acquisition(service, tmp_path)
    initial = service.storage.acquisition_get("acq-1")
    path = Path(initial["metadata"]["path"])
    before_bytes = path.read_bytes()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retired return must not reach the runtime")

    service._kobo_adapter = forbidden
    service.kobo_profiles = forbidden
    service.execute_drm_mutation = forbidden
    service._return_adept_acquisition = forbidden
    try:
        with pytest.raises(AttentionRequired) as caught:
            service.return_adept_acquisition(acquisition_id)
        assert caught.value.code == "loan_return_removed"
        assert "PRIVATE-IDENTIFIER" not in str(caught.value)
        assert calls == []
        assert service.storage.acquisition_get("acq-1") == initial
        assert path.read_bytes() == before_bytes
    finally:
        service.close()


def test_internal_legacy_adept_return_alias_refuses_before_any_service_access():
    # Even an old plugin calling the former private method cannot bypass the
    # public facade; neither call is permitted to acquire a filesystem lock.
    for method in (
        LibraryService.return_adept_acquisition,
        LibraryService._return_adept_acquisition,
    ):
        with pytest.raises(AttentionRequired) as caught:
            method(object(), "PRIVATE-IDENTIFIER")
        assert caught.value.code == "loan_return_removed"
        assert "PRIVATE-IDENTIFIER" not in str(caught.value)


def test_adept_return_lookup_is_inert_even_with_an_active_historical_binding(
    tmp_path: Path,
):
    service = _service(tmp_path)
    _acquisition(service, tmp_path)
    old = service.storage.acquisition_get("acq-1")
    old["artifact_id"] = "sha256:original-artifact"
    service.storage.acquisition_put(old)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("retired availability lookup cannot access runtime")

    service._adept_return_capability = forbidden
    service.kobo_profiles = forbidden
    before = service.storage.acquisition_get("acq-1")
    try:
        assert service.adept_return_for_artifact(
            "sha256:original-artifact", "default",
        ) is None
        assert service.adept_return_for_artifact(
            "sha256:original-artifact", "another",
        ) is None
        assert service.storage.acquisition_get("acq-1") == before
    finally:
        service.close()


def test_adept_return_lookup_never_dereferences_legacy_caller():
    assert LibraryService.adept_return_for_artifact(
        object(), "sha256:artifact", "default",
    ) is None


def test_retired_return_does_not_rewrite_a_historical_return_state(tmp_path: Path):
    service = _service(tmp_path)
    _acquisition(service, tmp_path)
    old = service.storage.acquisition_get("acq-1")
    old["metadata"]["adept_binding"].update(
        loan_state="returned", token_present=False,
        return_available=False, return_verification="confirmed",
    )
    service.storage.acquisition_put(old)
    before = service.storage.acquisition_get("acq-1")
    try:
        with pytest.raises(AttentionRequired) as caught:
            service.return_adept_acquisition("acq-1")
        assert caught.value.code == "loan_return_removed"
        assert service.storage.acquisition_get("acq-1") == before
    finally:
        service.close()


def test_core_adept_return_helper_is_inert_even_for_direct_legacy_callers():
    from cifereca.core.adept_acquisition_return import return_adept_acquisition

    with pytest.raises(AttentionRequired) as caught:
        return_adept_acquisition(
            object(), "private-acquisition",
            AttentionRequired=AttentionRequired,
            ProviderConfigurationError=object(),
            DrmMutationRequest=object(),
            time=object(),
        )
    assert caught.value.code == "loan_return_removed"
    assert "private-acquisition" not in str(caught.value)
