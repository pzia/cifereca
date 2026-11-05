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


def _status(connections, accounts, *, profile="reader-a", identity="alice",
            selected=None):
    return normal_paris_adept_status(
        identity_id=identity, connections=connections,
        accounts=accounts, profile_id=profile,
        primary_connection_id=selected,
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


def test_malformed_local_ids_cannot_fabricate_ready_adept_configuration():
    """An invalid ID's string representation is not an account/profile link."""
    from types import SimpleNamespace as NS

    valid = _connection("alice", "paris-a")
    primary = _primary("alice", "paris-a")
    assert _status([valid], [primary])["status"] == "ready"

    for bad in (True, 17, ["alice"], {"identity": "alice"}):
        assert _status([valid], [primary], identity=bad)["reason"] == "unknown_identity"
    for bad in (True, 17, ["profile"], {"profile": "reader-a"}):
        assert _status([valid], [primary], profile=bad)["reason"] == "missing_adept_profile"

    # Before validation, matching str(True) / str(17) on both sides could
    # present a locally "ready" connection that had never been identified.
    for bad in (True, 17, ["paris-a"], {"credential": "paris-a"}):
        connection = _connection("alice", bad)
        synthetic = NS(
            identity_id="alice", provider_id="paris",
            credential_id=str(bad), is_primary=True,
        )
        assert _status([connection], [synthetic])["reason"] == "missing_paris_connection"

        connection = _connection("alice", str(bad))
        synthetic.credential_id = bad
        assert _status([connection], [synthetic])["reason"] == "missing_primary_account"


def test_non_text_borrower_identity_never_matches_textual_identity():
    from types import SimpleNamespace as NS

    connection = _connection("17", "paris-a")
    bogus = NS(
        identity_id=17, provider_id="paris",
        credential_id="paris-a", is_primary=True,
    )
    assert _status([connection], [bogus], identity="17")["reason"] == "missing_primary_account"
    assert _status([connection], [_primary("17", "paris-a")], identity="17")["status"] == "ready"

def test_explicit_primary_connection_disambiguates_two_accounts_without_fallback():
    rows = [_connection("alice", "paris-a"), _connection("alice", "paris-b")]
    accounts = [_primary("alice", "paris-a"), _primary("alice", "paris-b")]
    selected = _status(rows, accounts, selected="paris-b")
    assert selected["status"] == "ready"
    assert selected["adept_profile_id"] == "reader-a"
    assert _status(rows, accounts)["reason"] == "multiple_paris_connections"
    # Rebinding/disable of the chosen connection never selects the other.
    assert _status([rows[0], dict(rows[1], enabled=False)],
                   accounts, selected="paris-b")["reason"] == (
        "primary_paris_connection_missing"
    )
    assert _status(rows, [_primary("alice", "paris-a")],
                   selected="paris-b")["reason"] == "missing_primary_account"
    assert _status(rows, accounts, selected="not-configured")["status"] == "to_verify"


def test_invalid_duplicate_or_cross_identity_selected_connection_is_never_ready():
    rows = [_connection("alice", "paris-a"), _connection("alice", "paris-a")]
    primary = [_primary("alice", "paris-a")]
    assert _status(rows, primary, selected="paris-a")["reason"] == (
        "primary_paris_connection_duplicate"
    )
    for invalid in (True, 3, [], "", " paris-a ", "paris-b"):
        status = _status(rows[:1], primary, selected=invalid)
        assert status["status"] != "ready"
    assert _status([_connection("bob", "paris-a")], primary,
                   selected="paris-a")["status"] != "ready"


def test_service_facade_honours_persisted_choice_without_modifying_grants(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "alice-secondary", "label": "Paris secondaire",
        })
        service.accounts.append(_primary("alice", "alice-secondary"))
        assert service.normal_paris_adept_status("alice")["reason"] == (
            "multiple_paris_connections"
        )
        service.config.upsert_identity({
            "id": "alice", "label": "Alice", "directory_name": "alice",
            "primary_paris_connection_id": "alice-paris",
        })
        assert service.normal_paris_adept_status("alice")["status"] == "ready"
        # No exceptional grant or historic origin is created by the choice.
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        ) is None
        service.config.upsert_identity({
            "id": "alice", "label": "Alice", "directory_name": "alice",
            "primary_paris_connection_id": "offline",
        })
        assert service.normal_paris_adept_status("alice")["reason"] == (
            "primary_paris_connection_missing"
        )
    finally:
        service.close()
