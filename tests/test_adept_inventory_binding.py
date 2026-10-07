from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.locking import LockBusyError
from cifereca.plugin_api import AttentionRequired
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


def test_duplicate_exact_inventory_matches_are_ambiguous(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"].append(dict(inventory["profiles"][0]["loans"][0]))
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "ambiguous"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] is None
        assert binding["return_available"] is False
    finally:
        service.close()


def test_partial_inventory_cannot_establish_unique_binding(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["complete"] = False
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "inventory_error"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] is None
        assert binding["return_available"] is False
    finally:
        service.close()


def test_matching_title_with_old_migrated_path_is_not_binding_proof(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"][0].update({
        "local_book": "/previous/Livre.epub",
        "title": "Livre",
    })
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "unresolved"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] is None
        assert binding["return_available"] is False
    finally:
        service.close()


def test_conflicting_inventory_id_never_overwrites_existing_binding(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    row = service.storage.acquisition_get(acquisition_id)
    row["metadata"]["adept_binding"]["adept_loan_id"] = "original"
    service.storage.acquisition_put(row)
    runtime, calls = _runtime("hb", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "binding_conflict"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "original"
        assert binding["return_available"] is False
    finally:
        service.close()


def test_multiple_explicit_profiles_keep_namespace_and_ambiguity(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path, profile_id=None)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"].append({
        "profile": "Hugues",
        "loans": [dict(inventory["profiles"][0]["loans"][0])],
    })
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [
        {"profile_id": "stable-profile", "name": "hb"},
        {"profile_id": "other", "name": "Hugues"},
    ]
    try:
        result = service.reconcile_adept_acquisition(
            acquisition_id,
            profile_references=["stable-profile", "other"],
        )

        assert result["status"] == "ambiguous"
        assert calls[-1] == ["hb", "Hugues"]
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] is None
        assert binding["return_available"] is False
    finally:
        service.close()


def test_inventory_read_stays_available_when_return_capability_is_disabled(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    service._adept_return_capability = lambda: False
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert result["return_available"] is False
        assert calls == [["hb"]]
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "registry-id"
        assert binding["loan_state"] == "active"
    finally:
        service.close()


def test_expired_inventory_loan_disables_return(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"][0]["expired"] = True
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert result["return_available"] is False
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["loan_state"] == "expired"
        assert binding["expired"] is True
    finally:
        service.close()


def test_missing_inventory_return_token_disables_return(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"][0]["token_present"] = False
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert result["return_available"] is False
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["loan_state"] == "active"
        assert binding["token_present"] is False
    finally:
        service.close()


def test_returned_state_is_revalidated_from_public_inventory(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"][0].update({
        "returned": True,
        "token_present": False,
    })
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "resolved"
        assert result["return_available"] is False
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "registry-id"
        assert binding["loan_state"] == "returned"
        assert binding["return_verification"] == "confirmed"
        assert binding["token_present"] is False
    finally:
        service.close()


def test_missing_inventory_entry_preserves_historical_binding_and_artifact(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    row = service.storage.acquisition_get(acquisition_id)
    row["metadata"]["adept_binding"].update({
        "adept_loan_id": "historical-id",
        "loan_state": "active",
        "token_present": True,
        "return_available": True,
    })
    service.storage.acquisition_put(row)
    runtime, calls = _runtime("hb", path)
    inventory = runtime.loan_inventories(["hb"])
    inventory["profiles"][0]["loans"] = []
    runtime.loan_inventories = lambda names: (calls.append(list(names)) or inventory)
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        before = path.read_bytes()
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "unresolved"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "historical-id"
        assert binding["inventory_status"] == "unresolved"
        assert binding["return_available"] is False
        assert path.read_bytes() == before
        assert path.is_file()
    finally:
        service.close()


def test_missing_artifact_skips_inventory_lookup(tmp_path: Path):
    service = _service(tmp_path)
    missing = tmp_path / "EBooks" / "hugues" / "paris" / "missing.epub"
    acquisition_id = _acquisition(service, missing)
    calls = []
    service._kobo_adapter = lambda: SimpleNamespace(
        loan_inventories=lambda names: calls.append(list(names))
    )
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        result = service.reconcile_adept_acquisition(acquisition_id)

        assert result["status"] == "artifact_missing"
        assert calls == []
    finally:
        service.close()


def test_busy_lock_skips_inventory_lookup(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    calls = []
    service._kobo_adapter = lambda: SimpleNamespace(
        loan_inventories=lambda names: calls.append(list(names))
    )
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        with patch("cifereca.services.InterProcessLock") as lock:
            lock.return_value.__enter__.side_effect = LockBusyError("busy")
            try:
                service.reconcile_adept_acquisition(acquisition_id)
                raise AssertionError("busy reconciliation must fail")
            except AttentionRequired as exc:
                assert exc.code == "acquisition_busy"

        assert calls == []
    finally:
        service.close()


def test_identity_batch_reuses_inventory_for_same_profile_combination(tmp_path: Path):
    service = _service(tmp_path)
    first = _artifact(tmp_path)
    second = first.with_name("Livre-2.epub")
    second.write_bytes(b"existing-local-artifact-2")
    _acquisition(service, first, acquisition_id="acq-1")
    _acquisition(service, second, acquisition_id="acq-2")

    calls = []
    inventory = {
        "complete": True,
        "profiles": [{
            "profile": "hb",
            "loans": [
                {
                    "id": "registry-id-1",
                    "expired": False,
                    "returned": False,
                    "token_present": True,
                    "local_book": str(first),
                },
                {
                    "id": "registry-id-2",
                    "expired": False,
                    "returned": False,
                    "token_present": True,
                    "local_book": str(second),
                },
            ],
        }],
        "errors": [],
    }

    runtime = SimpleNamespace(
        loan_inventories=lambda names: (calls.append(list(names)) or inventory)
    )
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    try:
        summary = service._reconcile_identity_adept_inventory("default")

        assert summary["resolved"] == 2
        assert summary["errors"] == 0
        assert calls == [["hb"]]
    finally:
        service.close()
