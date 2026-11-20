"""#7161: production profile lookup uses installed runtime, not a retired seam."""
from types import SimpleNamespace as NS

import pytest

from cifereca.identities import AdeptProfileResolutionError
from cifereca.services import LibraryService
from tests.test_sync_state_contract import _service


def setup_runtime(service, *, rows=None, refusal=False, revoked=False):
    events = []
    def profiles():
        events.append("profile-list")
        if refusal:
            raise RuntimeError("synthetic runtime failure")
        if revoked:
            manager.registry_generation += 1
        return {"profiles": rows}
    runtime = NS(profiles=profiles)
    def status(key):
        assert key == "runtime.kobo-adept"
        return NS(enabled=True, state="ready")
    def installed(key):
        assert key == "runtime.kobo-adept"
        return runtime
    manager = NS(status=status, service=installed, registry_generation=1)
    service._plugin_manager = manager
    return events


def test_stable_binding_resolves_actual_cli_name_without_legacy_adapter(tmp_path):
    service = _service(tmp_path)
    try:
        assert not hasattr(LibraryService, "_kobo_adapter")
        calls = setup_runtime(service, rows=[
            {"profile_id": "stable-a", "name": "reader-a", "fetch_capable": True},
            {"profile_id": "stable-b", "name": "reader-b", "fetch_capable": True},
        ])
        service.storage.adept_profile_binding_set("identity", "default", "stable-a")
        service.storage.adept_profile_binding_set("identity", "alex", "stable-b")
        assert service.resolve_adept_profile_for_identity("default") == ("reader-a", "identity")
        assert service.resolve_adept_profile_for_identity("alex") == ("reader-b", "identity")
        assert calls == ["profile-list", "profile-list"]
        assert service.storage.adept_profile_binding_get("identity", "default") == "stable-a"
        assert service.storage.adept_profile_binding_get("identity", "alex") == "stable-b"
    finally:
        service._plugin_manager = None
        service.close()


@pytest.mark.parametrize("scenario", ["unavailable", "revoked", "invalid", "missing"])
def test_failed_profile_inventory_never_becomes_cli_profile_id(tmp_path, scenario):
    service = _service(tmp_path)
    try:
        service.storage.adept_profile_binding_set("identity", "default", "stable-a")
        if scenario != "missing":
            setup_runtime(service, rows=None if scenario == "invalid" else [
                {"profile_id": "stable-a", "name": "reader-a", "fetch_capable": True},
            ], refusal=scenario == "unavailable", revoked=scenario == "revoked")
        with pytest.raises(AdeptProfileResolutionError) as error:
            service.resolve_adept_profile_for_identity("default")
        assert error.value.code == "adept_profile_lookup_failed"
        assert service.storage.adept_profile_binding_get("identity", "default") == "stable-a"
    finally:
        service._plugin_manager = None
        service.close()
