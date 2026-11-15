"""P0 #5851: simultaneous historical and v16 grants cannot select different owners."""
from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


SCOPE = dict(provider_id="paris", credential_id="alice-paris", account_id="linked-123")


def test_dual_grants_same_owner_remain_usable_without_migration(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        seed_historical_adept_binding(service, 
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
    finally:
        service.close()


def test_dual_grants_different_owners_fail_closed(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        seed_historical_adept_binding(service, 
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="alice", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_profile_changed")
    finally:
        service.close()




def test_v16_revocation_cannot_resurrect_archived_v15_authority(tmp_path):
    """A direct v16 grant + revoke must be as monotone as reconfirmation."""
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
        service.storage.adept_borrower_grant_delete(**SCOPE)
        archived = service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )
        assert archived["borrower_identity_id"] == "bob"
        assert archived["retired_for_authority"] is True
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        # A new explicit v16 grant can authorize a different borrower: the
        # archived conflicting v15 row remains descriptive, never a veto.
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="alice", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("alice", None)
        service.storage.adept_borrower_grant_delete(**SCOPE)
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_v16_revocation_tombstone_is_scoped_and_persists_on_reopen(tmp_path):
    from cifereca.storage import Storage

    service, _ = service_with_linked_borrower(tmp_path)
    path = service.storage.path
    try:
        _persist_account(service.storage)
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_borrower_grant_delete(**SCOPE)
    finally:
        service.close()
    reopened = Storage(path)
    try:
        assert reopened.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )["retired_for_authority"] is True
        assert reopened.paris_borrower_adept_bindings(
            credential_id="alice-paris",
        )[0]["borrower_identity_id"] == "bob"
    finally:
        reopened.close()


def test_v16_profile_invalidation_retires_historical_authority(tmp_path):
    """Database cascades must revoke legacy authority, not only the API."""
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_profile_binding_set("identity", "bob", "reader-new")
        assert service.storage.adept_borrower_grant_get(**SCOPE) is None
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )["retired_for_authority"] is True
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_legacy_only_row_remains_readable_until_explicit_cutover(tmp_path):
    """An absent v16 row must not manufacture a revocation tombstone."""
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_borrower_grant_delete(**SCOPE)
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )["retired_for_authority"] is False
    finally:
        service.close()


def test_revoked_primary_exception_cannot_fallback_to_connection_identity(tmp_path):
    from dataclasses import replace

    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        with service.storage._lock, service.storage._connect() as db:
            connection_id = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='paris' AND credential_id='alice-paris'"
            ).fetchone()[0]
        service.storage.provider_account_put({
            "connection_id": connection_id, "provider_id": "paris",
            "credential_id": "alice-paris", "identity_id": "alice",
            "account_id": "primary", "is_primary": True,
        })
        primary = replace(loan, source_account_id="primary")
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="primary",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            provider_id="paris", credential_id="alice-paris",
            account_id="primary", borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_borrower_grant_delete(
            provider_id="paris", credential_id="alice-paris",
            account_id="primary",
        )
        assert service._paris_adept_identity_for_loan(
            primary, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()
