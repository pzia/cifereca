"""P0 #5851: simultaneous historical and v16 grants cannot select different owners."""
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


SCOPE = dict(provider_id="paris", credential_id="alice-paris", account_id="linked-123")


def test_dual_grants_same_owner_remain_usable_without_migration(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.bind_paris_borrower_adept(
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
        service.bind_paris_borrower_adept(
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


def test_revoked_v16_while_legacy_present_does_not_infer_new_owner(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.bind_adept_borrower_grant(
            **SCOPE, borrower_identity_id="alice", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_profile_changed")
        service.storage.adept_borrower_grant_delete(**SCOPE)
        # Historical grant was never reconfirmed; this remains legacy behavior.
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
    finally:
        service.close()
