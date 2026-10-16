"""Kobo personal-library rows cannot outlive their source binding (#1346)."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cifereca.services import LibraryService


class FakeStorage:
    def __init__(self):
        self.owner = "alpha"

    def identity_for_connection(self, provider, credential):
        assert provider == "kobo" and credential == "credential"
        return self.owner

    def provider_connections(self, *, identity_id):
        if identity_id != self.owner:
            return []
        return [{
            "provider_id": "kobo", "credential_id": "credential",
            "enabled": True,
        }]


def fixture(mutation=None):
    storage = FakeStorage()
    account = SimpleNamespace(
        provider_id="kobo", credential_id="credential",
        unique_id="primary", identity_id="alpha",
    )
    session = SimpleNamespace(
        provider_id="kobo", credential_id="credential", connected=True,
        capabilities=SimpleNamespace(personal_library=True),
    )
    service = SimpleNamespace(
        storage=storage, accounts=[account], enabled=True,
        _sessions={("kobo", "credential"): session},
        _session_key=lambda provider, credential: (provider, credential),
        identity_by_id=lambda identity_id: (
            SimpleNamespace(identity_id=identity_id)
            if identity_id == "alpha" else None
        ),
    )
    service.sync_source_enabled = lambda account: service.enabled
    service._mirror_remote_results = Mock(return_value=2)

    def library():
        if mutation == "rebind":
            storage.owner = "beta"
        elif mutation == "disable":
            service.enabled = False
        elif mutation == "remove":
            service.accounts.clear()
        elif mutation == "session_replaced":
            service._sessions[("kobo", "credential")] = SimpleNamespace()
        elif mutation == "session_disconnected":
            session.connected = False
        return []

    session.library = library
    return service


@pytest.mark.parametrize("mutation", [
    "rebind", "disable", "remove", "session_replaced", "session_disconnected",
])
def test_kobo_mirror_discards_private_rows_if_binding_changes_mid_fetch(mutation):
    service = fixture(mutation)
    with patch(
        "cifereca.core.kobo_manifest_rights.mirror_kobo_manifest_rights",
        side_effect=AssertionError("stale rights cannot be persisted"),
    ) as rights:
        result = LibraryService.mirror_personal_libraries(service)
    assert not result
    assert result.blocked_sources == 1
    assert result.failed_sources == 0
    rights.assert_not_called()
    service._mirror_remote_results.assert_not_called()


def test_kobo_mirror_preserves_healthy_source_inventory_only():
    service = fixture()
    with patch(
        "cifereca.core.kobo_manifest_rights.mirror_kobo_manifest_rights",
        return_value=0,
    ) as rights:
        result = LibraryService.mirror_personal_libraries(service)
    assert result == {"kobo:credential": 2}
    assert result.blocked_sources == 0
    assert result.failed_sources == 0
    rights.assert_called_once()
    service._mirror_remote_results.assert_called_once()
