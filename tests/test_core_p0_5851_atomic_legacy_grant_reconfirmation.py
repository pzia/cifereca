"""P0 #5851: no window between reconfirmed v16 grant and Paris retirement."""
import sqlite3

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


def scope(provider="paris"):
    return dict(provider_id=provider, credential_id="alice-paris", account_id="linked-123")


def legacy(service):
    return service.storage.paris_borrower_adept_binding_get(
        credential_id="alice-paris", account_id="linked-123",
    )


def grant(service):
    return service.storage.adept_borrower_grant_get(**scope())


def linked_identity(service, loan):
    return service._paris_adept_identity_for_loan(
        loan, connection_identity_id="alice",
    )


def setup_legacy(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    _persist_account(service.storage)
    service.bind_paris_borrower_adept(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id="bob", confirmed=True,
    )
    return service, loan


def test_explicit_reconfirmation_retires_legacy_in_one_transaction(tmp_path):
    service, loan = setup_legacy(tmp_path)
    try:
        assert legacy(service) is not None
        assert grant(service) is None
        assert linked_identity(service, loan) == ("bob", None)
        proof = service.reconfirm_legacy_adept_borrower_grant(
            **scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert proof["confirmed_by"] == "explicit"
        assert proof["borrower_identity_id"] == "bob"
        assert legacy(service) is None
        assert linked_identity(service, loan) == ("bob", None)
        service.storage.adept_borrower_grant_delete(**scope())
        assert grant(service) is None
        # The second preflight must not revive the formerly confirmed row.
        assert linked_identity(service, loan) == (None, "adept_borrower_binding_required")
        assert legacy(service) is None
    finally:
        service.close()


def test_failed_legacy_retirement_rolls_back_new_v16_grant(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_legacy_retirement
                BEFORE DELETE ON paris_borrower_adept_bindings
                BEGIN SELECT RAISE(ABORT, 'simulated retirement failure'); END
            """)
        with pytest.raises(sqlite3.DatabaseError):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(), borrower_identity_id="bob", confirmed=True,
            )
        assert legacy(service) is not None
        assert grant(service) is None
    finally:
        service.close()


@pytest.mark.parametrize("bad", [False, None, 1, "true"])
def test_reconfirmation_without_native_consent_has_no_side_effects(tmp_path, bad):
    service, _ = setup_legacy(tmp_path)
    try:
        with pytest.raises(ValueError):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(), borrower_identity_id="bob", confirmed=bad,
            )
        assert legacy(service) is not None
        assert grant(service) is None
    finally:
        service.close()


def test_reconfirmation_requires_matching_persisted_account(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        # A plugin-published account without an attested SQLite account
        # never authorizes writing the scoped v16 grant or retiring legacy.
        with pytest.raises(ValueError, match="evidence_invalid"):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(), borrower_identity_id="bob", confirmed=True,
            )
        assert legacy(service) is not None
        assert grant(service) is None
    finally:
        service.close()


def test_non_paris_reconfirmation_cannot_delete_paris_legacy(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        with pytest.raises(ValueError, match="evidence_invalid"):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(provider="kobo"), borrower_identity_id="bob",
                confirmed=True,
            )
        assert legacy(service) is not None
        assert grant(service) is None
    finally:
        service.close()


def test_reconfirmation_refuses_nonexistent_legacy_without_new_grant(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        with pytest.raises(ValueError, match="legacy_grant_not_found"):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(), borrower_identity_id="bob", confirmed=True,
            )
        assert grant(service) is None
    finally:
        service.close()


def test_reconfirmation_uses_current_profile_and_never_copies_old_profile(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        service.storage.adept_profile_binding_set("identity", "bob", "reader-new")
        proof = service.reconfirm_legacy_adept_borrower_grant(
            **scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert proof["profile_id"] == "reader-new"
        assert legacy(service) is None
    finally:
        service.close()
