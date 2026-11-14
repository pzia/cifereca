"""P0 #5851: ADEPT borrower source may use an explicitly confirmed v16 grant.

A legacy Paris binding is never auto-promoted and a deleted v16 grant cannot
silently fall back to another identity or an entitlement.
"""
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


def _coords():
    return dict(provider_id="paris", credential_id="alice-paris",
                account_id="linked-123")


def _resolve(service, loan):
    return service._paris_adept_identity_for_loan(
        loan, connection_identity_id="alice",
    )


def test_v16_exact_confirmed_linked_grant_is_read_without_legacy_promotion(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        assert _resolve(service, loan) == (None, "adept_borrower_binding_required")
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None

        service.bind_adept_borrower_grant(
            **_coords(), borrower_identity_id="bob", confirmed=True,
        )
        # Only source-scope v16 can attest Bob. A foreign UI selection is inert.
        service.current_identity = service.identity_by_id("alice")
        assert _resolve(service, loan) == ("bob", None)
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None

        service.storage.adept_borrower_grant_delete(**_coords())
        assert _resolve(service, loan) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_profile_rotation_and_restoration_do_not_reactivate_v16_borrower(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.bind_adept_borrower_grant(
            **_coords(), borrower_identity_id="bob", confirmed=True,
        )
        assert _resolve(service, loan) == ("bob", None)
        service.storage.adept_profile_binding_set("identity", "bob", "reader-new")
        assert _resolve(service, loan) == (None, "adept_borrower_binding_required")
        service.storage.adept_profile_binding_set("identity", "bob", "bob-profile")
        assert _resolve(service, loan) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_foreign_provider_grant_never_attests_paris_linked_loan(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.storage.provider_connection_put({
            "provider_id": "kobo", "credential_id": "alice-paris",
            "identity_id": "alice", "label": "Synthetic Kobo",
        })
        _persist_account(service.storage, provider="kobo")
        service.storage.adept_borrower_grant_put(
            provider_id="kobo", credential_id="alice-paris",
            account_id="linked-123", connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        assert _resolve(service, loan) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_existing_legacy_grant_is_preserved_without_silent_v16_migration(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service.storage.adept_borrower_grant_get(**_coords()) is None
        assert _resolve(service, loan) == ("bob", None)
        assert service.storage.adept_borrower_grant_get(**_coords()) is None
    finally:
        service.close()
