"""#4673: real Core/SQLite, two Paris sessions, zero HTTP/POST.

A public catalogue match does not transport a borrowable account between
identities. UI readiness and its diagnostic must agree across A→B→A.
"""
from dataclasses import replace

from cifereca.brokers import IdentityBrokerImpl
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.models import Account
from tests.test_paris_manual_borrow_dispatch import setup, ParisProviderSession


def _add_second_paris(service, account):
    other = replace(account, identity_id="alex", credential_id="card-B")
    session = ParisProviderSession(
        credential_id="card-B", label="Paris B", credentials={},
        options={}, config={}, diagnostics=service.diagnostics,
        identity_broker=IdentityBrokerImpl(service),
    )
    session.connected = True
    session._accounts = [other]
    service._sessions[service._session_key("paris", "card-B")] = session
    service.accounts.append(other)
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "card-B", "label": "Paris B",
        "enabled": True, "metadata": {},
    })
    return other, session


def test_two_profiles_never_reuse_other_account_or_edition(tmp_path):
    service, session_a, account_a, item_a, state = setup(tmp_path)
    try:
        account_b, session_b = _add_second_paris(service, account_a)
        item_b = replace(item_a, source_credential="card-B", id="paris:edition-b")
        assert account_b.identity_id != account_a.identity_id
        assert account_b.credential_id != account_a.credential_id

        for identity, allowed, wrong in (
            ("default", item_a, item_b),
            ("alex", item_b, item_a),
            ("default", item_a, item_b),
        ):
            service.current_identity = service.identity_by_id(identity)
            assert service.current_identity is not None
            ready = service.paris_borrow_diagnostic(allowed)
            assert ready == {
                "local_action_possible": True,
                "reason_code": "local_action_available_provider_unknown",
                "provider_eligibility": "unknown",
            }
            assert service.can_borrow_paris(allowed) is True
            wrong_result = service.paris_borrow_diagnostic(wrong)
            assert wrong_result["local_action_possible"] is False
            assert wrong_result["provider_eligibility"] == "unknown"
            assert not service.can_borrow_paris(wrong)

        # A read-only projection must not create or consume a mutation claim
        # for either account, even after the A/B/A profile transition.
        assert state["calls"] == []
        assert state["mutations"] == 0
        assert service.storage.metadata_get(borrow_claim_key(account_a, item_a)) is None
        assert service.storage.metadata_get(borrow_claim_key(account_b, item_b)) is None
    finally:
        service.close()


def test_other_profile_never_inherits_removed_own_connection(tmp_path):
    service, session_a, account_a, item_a, state = setup(tmp_path)
    try:
        account_b, session_b = _add_second_paris(service, account_a)
        item_b = replace(item_a, source_credential="card-B", id="paris:edition-b")
        service.current_identity = service.identity_by_id("alex")
        assert service.paris_borrow_diagnostic(item_b)["local_action_possible"] is True
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-B", "label": "Paris B",
            "enabled": False, "metadata": {},
        })
        rejected = service.paris_borrow_diagnostic(item_b)
        assert rejected["reason_code"] == "primary_paris_connection_mismatch_or_inactive"
        assert service.can_borrow_paris(item_b) is False
        service.current_identity = service.identity_by_id("default")
        assert service.can_borrow_paris(item_a) is True
        assert state["mutations"] == 0
        assert state["calls"] == []
    finally:
        service.close()
