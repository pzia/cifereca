"""P0 #1063: normal Paris/ADEPT status never infers an Adobe owner."""
from __future__ import annotations

from cifereca.core.paris_adept_normal import normal_paris_adept_status
from cifereca.models import Account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _connection(identity, credential, *, enabled=True):
    return {"provider_id": "paris", "identity_id": identity,
            "credential_id": credential, "label": "Paris principal", "enabled": enabled}


def _primary(identity, credential, *, primary=True):
    return Account(provider_id="paris", identity_id=identity,
                   credential_id=credential, unique_id="", is_primary=primary)


def _status(connections, accounts, *, profile="reader-a", identity="alice"):
    return normal_paris_adept_status(
        identity_id=identity, connections=connections,
        accounts=accounts, profile_id=profile,
    )


def test_one_exact_primary_and_profile_is_locally_ready_only():
    result = _status([_connection("alice", "paris-a")], [_primary("alice", "paris-a")])
    assert result["status"] == "ready"
    assert result["adept_profile_id"] == "reader-a"
    assert result["reason"] == "local_configuration_complete"


def test_missing_disabled_and_unknown_connections_fail_closed():
    assert _status([], [])["reason"] == "missing_paris_connection"
    assert _status([_connection("alice", "paris-a", enabled=False)], [])["status"] == "to_configure"
    assert _status([_connection("bob", "paris-a")], [])["status"] == "to_configure"
    assert _status([_connection("alice", "paris-a")], [], profile=None)["reason"] == "missing_adept_profile"


def test_multiple_paris_connections_require_explicit_selection_not_first():
    result = _status(
        [_connection("alice", "paris-a"), _connection("alice", "paris-b")],
        [_primary("alice", "paris-a"), _primary("alice", "paris-b")],
    )
    assert result["status"] == "to_verify"
    assert result["reason"] == "multiple_paris_connections"
    assert result["adept_profile_id"] is None


def test_nonprimary_and_other_connection_cannot_prove_primary_account():
    result = _status([_connection("alice", "paris-a")], [
        _primary("alice", "paris-a", primary=False),
        _primary("alice", "paris-b"),
        _primary("bob", "paris-a"),
    ])
    assert result["reason"] == "missing_primary_account"


def test_duplicate_primary_accounts_fail_closed():
    result = _status([_connection("alice", "paris-a")], [
        _primary("alice", "paris-a"), _primary("alice", "paris-a"),
    ])
    assert result["reason"] == "multiple_primary_accounts"
    assert result["status"] == "to_verify"


def test_unrelated_secondary_and_kobo_do_not_change_exact_primary():
    other = Account(provider_id="kobo", identity_id="alice",
                    credential_id="paris-a", is_primary=True)
    result = _status([_connection("alice", "paris-a"),
                      {"provider_id": "kobo", "identity_id": "alice",
                       "credential_id": "kobo", "enabled": True}],
                     [_primary("alice", "paris-a"), _primary("alice", "paris-a", primary=False), other])
    assert result["status"] == "ready"


def test_service_facade_is_identity_scoped_and_never_calls_adobe(tmp_path):
    service, _loan = service_with_linked_borrower(tmp_path)
    try:
        service.kobo_profiles = lambda: (_ for _ in ()).throw(AssertionError("No ADEPT runtime"))
        service.acquire_digital_loan = lambda *_: (_ for _ in ()).throw(AssertionError("No ACSM"))
        status = service.normal_paris_adept_status("alice")
        assert status["status"] == "ready"
        assert status["adept_profile_id"] == "alice-profile"
        assert service.normal_paris_adept_status("bob")["reason"] == "missing_paris_connection"
        assert service.normal_paris_adept_status("missing")["reason"] == "unknown_identity"
    finally:
        service.close()


def test_ui_identity_switch_does_not_change_other_identity_status(tmp_path):
    service, _loan = service_with_linked_borrower(tmp_path)
    try:
        original = service.normal_paris_adept_status("alice")
        service.select_identity("bob")
        assert service.normal_paris_adept_status("alice") == original
        service.storage.adept_profile_binding_set("identity", "alice", None)
        assert service.normal_paris_adept_status("alice")["reason"] == "missing_adept_profile"
        # Changing the normal binding cannot mutate exceptional borrower grants.
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        ) is None
    finally:
        service.close()
