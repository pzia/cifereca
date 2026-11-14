"""P0 #5851: v16 explicit grant writer and borrower reader must agree."""
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


def _scope():
    return dict(
        provider_id="paris", credential_id="alice-paris",
        account_id="linked-123",
    )


def _read(service, *, provider_id="paris", connection_identity_id="alice"):
    return service.explicit_adept_borrower_grant_for_source(
        provider_id=provider_id, credential_id="alice-paris",
        account_id="linked-123", connection_identity_id=connection_identity_id,
    )


def test_writer_reader_roundtrip_requires_new_explicit_confirmation(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.current_identity = service.identity_by_id("bob")
        assert _read(service) == (None, "adept_borrower_binding_required")
        # Even a confirmed legacy row is not a new v16 grant.
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert _read(service) == (None, "adept_borrower_binding_required")
        proof = service.bind_adept_borrower_grant(
            **_scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert proof["confirmed_by"] == "explicit"
        assert _read(service) == ("bob", None)
        # GTK selection cannot change borrower authority.
        service.current_identity = service.identity_by_id("alice")
        assert _read(service) == ("bob", None)
        assert _read(service, provider_id="kobo")[0] is None
        assert _read(service, connection_identity_id="bob")[0] is None
        # Revocation leaves the old Paris row present but never restores v16.
        service.storage.adept_borrower_grant_delete(**_scope())
        assert _read(service) == (None, "adept_borrower_binding_required")
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is not None
    finally:
        service.close()


def test_revocation_between_preflight_and_fulfillment_denies_same_v16_proof(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.bind_adept_borrower_grant(
            **_scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert _read(service) == ("bob", None)
        # Model an explicit revoke during provider staging. The second
        # preflight must freshly read authority, never reuse the first result.
        service.storage.adept_borrower_grant_delete(**_scope())
        assert _read(service) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


def test_profile_rotation_and_restoration_cannot_reanimate_v16_authority(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        service.bind_adept_borrower_grant(
            **_scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert _read(service) == ("bob", None)
        service.storage.adept_profile_binding_set(
            "identity", "bob", "new-reader",
        )
        assert _read(service)[0] is None
        service.storage.adept_profile_binding_set(
            "identity", "bob", "bob-profile",
        )
        assert _read(service) == (None, "adept_borrower_binding_required")
    finally:
        service.close()
