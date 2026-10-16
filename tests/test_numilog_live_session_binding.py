"""Only the current connected Numilog session may attest private purchases."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cifereca.core.numilog_manifest_rights import (
    numilog_inventory_binding_valid,
)
from cifereca.services import LibraryService


def source():
    account = SimpleNamespace(
        provider_id="numilog", credential_id="secret-ref",
        unique_id="account", identity_id="alice",
    )
    session = SimpleNamespace(
        provider_id="numilog", credential_id="secret-ref", connected=True,
        capabilities=SimpleNamespace(personal_library=True),
    )
    storage = SimpleNamespace(
        identity_for_connection=lambda provider, credential: "alice",
        provider_connections=lambda *, identity_id: [{
            "provider_id": "numilog", "credential_id": "secret-ref",
            "enabled": True,
        }],
        entitlements=Mock(side_effect=AssertionError("stale rights read")),
    )
    service = SimpleNamespace(
        accounts=[account], storage=storage,
        _sessions={("numilog", "secret-ref"): session},
        _session_key=lambda provider, credential: (provider, credential),
        sync_source_enabled=lambda account: True,
        identity_by_id=lambda identity_id: (
            SimpleNamespace(identity_id="alice") if identity_id == "alice" else None
        ),
        _mirror_remote_results=Mock(return_value=1),
    )
    session.library = lambda: []
    return service, account, session


def test_current_session_is_eligible_for_private_inventory():
    service, account, session = source()
    assert numilog_inventory_binding_valid(service, account, session=session)


def test_replaced_session_cannot_attest_owned_rights():
    service, account, stale = source()
    service._sessions[("numilog", "secret-ref")] = SimpleNamespace(
        provider_id="numilog", credential_id="secret-ref", connected=True,
        capabilities=SimpleNamespace(personal_library=True),
    )
    assert not numilog_inventory_binding_valid(service, account, session=stale)
    service.storage.entitlements.assert_not_called()


def test_ambiguous_numilog_sessions_are_not_ownership_evidence():
    service, account, session = source()
    service._sessions[("duplicate", "secret-ref")] = SimpleNamespace(
        provider_id="numilog", credential_id="secret-ref", connected=True,
        capabilities=SimpleNamespace(personal_library=True),
    )
    assert not numilog_inventory_binding_valid(service, account, session=session)


def test_mirror_disposes_of_inflight_numilog_rows_after_session_change():
    service, account, session = source()

    def stale_library():
        service._sessions[("numilog", "secret-ref")] = SimpleNamespace(
            provider_id="numilog", credential_id="secret-ref", connected=True,
        )
        return []

    session.library = stale_library
    with patch(
        "cifereca.core.numilog_manifest_rights.mirror_numilog_manifest_rights",
        side_effect=AssertionError("stale rights must not be projected"),
    ) as rights:
        summary = LibraryService.mirror_personal_libraries(service)
    assert not summary
    assert summary.blocked_sources == 1
    assert summary.failed_sources == 0
    rights.assert_not_called()
    service._mirror_remote_results.assert_not_called()
