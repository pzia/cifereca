"""P0 #3719: old automatic loan/purchase ON is never another account's ON.

Fixtures are local: no actual BnF/Numilog account, network or ADEPT.
"""
from __future__ import annotations

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_auto_consent_bridge import (
    legacy_source_auto_policy, _source_id,
)
from cifereca.core.source_sync_consent import scope_for_account
from cifereca.core.source_account_binding import source_account_binding_ready
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core




def _historical_legacy_on(service, account):
    """Exercise the same exact account-binding fence as Core's real executor."""
    if not source_account_binding_ready(
        service, account, expected_provider_id=account.provider_id,
    ):
        return False
    return legacy_source_auto_policy(
        service.storage, scope_for_account(account), "loan",
    )[0] == "on"

@pytest.mark.parametrize(("first", "rebound"), [
    ("", "bnf-pret:bnf-card:primary"),
    ("", " \t "),
    ("readership", " readership "),
])
def test_bnf_old_on_never_transfers_to_aliased_borrower(
    connected_core, first, rebound,
):
    service, session, account, loan, _state, downloaded = connected_core
    # BnF's real credential is provider-specific in the fixture.
    if rebound == "bnf-pret:bnf-card:primary":
        rebound = "bnf-pret:" + account.credential_id + ":primary"
    # Keep the fixture's durable account register aligned with the initial
    # borrower. The Core now correctly refuses an in-memory account that is
    # absent from provider_accounts before any policy can be enabled.
    # Only the SECOND, aliased borrower remains deliberately out of sync.
    with service.storage._lock, service.storage._connect() as db:
        updated = db.execute(
            "UPDATE provider_accounts SET account_id=? "
            "WHERE identity_id=? AND provider_id=? AND credential_id=?",
            (first, account.identity_id, "bnf-pret", account.credential_id),
        )
        assert updated.rowcount == 1
    account.unique_id = first
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(account)
    prefix = "sync.source.bnf_auto."
    key = prefix + _source_id(scope_for_account(account))
    service.storage.metadata_set_with_revision(key, "1", key + ".revision")
    assert _historical_legacy_on(service, account) is True

    account.unique_id = rebound
    assert broker._source_id(account) == source_id
    assert len(session.accounts) == len(service.accounts) == 1
    assert _historical_legacy_on(service, account) is False
    assert not downloaded
    assert service.storage.metadata_get(
        "sync.source.bnf_auto." + _source_id(scope_for_account(account))
    ) == "1"
    account.unique_id = first
    assert _historical_legacy_on(service, account) is True


