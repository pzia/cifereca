from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service._adept_return_capability = lambda: True
    service.execute_acquisition_request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("inventory reconciliation must never redownload")
    )
    return service


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "EBooks" / "hugues" / "paris" / "Livre.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"existing-local-artifact")
    return path


def _acquisition(
    service: LibraryService,
    path: Path,
    *,
    profile_id: str | None = "stable-profile",
    acquisition_id: str = "acq-1",
) -> str:
    return service.storage.acquisition_put({
        "acquisition_id": acquisition_id,
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
                "adept_profile_id": profile_id,
                "adept_profile_name": "hb" if profile_id else None,
                "adept_loan_id": None,
                "return_available": False,
            },
        },
    })


def _runtime(profile_name: str, path: Path):
    calls: list[list[str]] = []

    def loan_inventories(names):
        calls.append(list(names))
        return {
            "complete": True,
            "profiles": [{
                "profile": profile_name,
                "loans": [{
                    "id": "registry-id",
                    "expired": False,
                    "returned": False,
                    "token_present": True,
                    "local_book": str(path),
                }],
            }],
            "errors": [],
        }

    return SimpleNamespace(loan_inventories=loan_inventories), calls


def test_missing_adept_id_is_restored_from_unique_public_inventory_match(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        before = path.read_bytes()
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert result["return_available"] is True
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "registry-id"
        assert binding["adept_profile_id"] == "stable-profile"
        assert binding["binding_source"] == "public_inventory"
        assert path.read_bytes() == before
        assert calls == [["hb"]]
    finally:
        service.close()


def test_origin_profile_id_resolves_renamed_profile_before_identity_binding(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    service.storage.adept_profile_binding_set("identity", "default", "different")
    runtime, calls = _runtime("Héloïse", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [
        {"profile_id": "stable-profile", "name": "Héloïse"},
        {"profile_id": "different", "name": "Hugues"},
    ]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert calls == [["Héloïse"]]
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_profile_id"] == "stable-profile"
        assert binding["adept_profile_name"] == "Héloïse"
    finally:
        service.close()


def test_identity_profile_is_used_only_when_origin_profile_is_absent(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path, profile_id=None)
    service.storage.adept_profile_binding_set("identity", "default", "stable-profile")
    runtime, calls = _runtime("hb", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert calls == [["hb"]]
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_profile_id"] == "stable-profile"
    finally:
        service.close()


def test_missing_origin_and_identity_binding_never_uses_implicit_profile(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path, profile_id=None)
    runtime, calls = _runtime("hb", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "profile_missing"
        assert calls == []
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] is None
        assert binding["return_available"] is False
    finally:
        service.close()
