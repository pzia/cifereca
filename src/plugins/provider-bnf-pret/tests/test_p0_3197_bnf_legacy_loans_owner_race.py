"""#3197/#3252: legacy BnF loans cannot survive an in-flight owner rebind.

One fake activity() call; never SSO, loan tokens, ACSM or real network.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p1_3252_rights_observation_snapshot import fixture, activity


@pytest.mark.parametrize("race", [
    "identity_rebound", "account_changed", "credential_rebound",
    "provider_rebound", "account_replaced_same_values",
    "account_removed", "disconnected",
])
def test_bnf_legacy_loans_discard_inflight_ambiguous_account_and_token_state(race):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    original = client.activity

    def during_activity():
        response = original()
        if race == "identity_rebound":
            account.identity_id = "identity-B"
        elif race == "account_changed":
            account.unique_id = "other-account"
        elif race == "credential_rebound":
            account.credential_id = "other-credential"
        elif race == "provider_rebound":
            account.provider_id = "paris"
        elif race == "account_replaced_same_values":
            session._accounts = [replace(account)]
        elif race == "account_removed":
            session._accounts = []
        else:
            session.connected = False
        return response

    client.activity = during_activity
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login", "activity"]
        # A stale token lookup is not justified by this failed legacy read.
        assert session._observed_loans == {}
    finally:
        session.close()


def test_bnf_legacy_loans_refuse_mismatched_active_credential_before_activity():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    account.credential_id = "other-credential"
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login"]
        assert session._observed_loans == {}
    finally:
        session.close()


def test_bnf_legacy_loans_stable_scope_still_delivers_one_positive_loan():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    try:
        rows = session.loans(account)
        assert [(x.id, x.record_id, x.identity_id) for x in rows] == [
            ("loan-1", "edition-1", "identity-A"),
        ]
        assert session._observed_loans == {"loan-1": "edition-1"}
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
