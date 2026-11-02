"""P0 #3719: old automatic loan/purchase ON is never another account's ON.

Fixtures are local: no actual BnF/Numilog account, network or ADEPT.
"""
from __future__ import annotations

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import bnf_auto_materialization as bnf
from cifereca.core.numilog_local_preview import _Binding, _valid_binding
from cifereca.core.numilog_materialization_consent import prepare
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_core_p0_numilog_auto_materialization import _setup


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
    assert broker.set_bnf_auto_download(source_id, True)
    assert bnf.enabled(service, account) is True

    account.unique_id = rebound
    assert broker._source_id(account) == source_id
    assert len(session.accounts) == len(service.accounts) == 1
    assert bnf.enabled(service, account) is False
    assert bnf.materialize(service, account, loan)["status"] == "skipped"
    assert not downloaded
    assert service.storage.metadata_get(bnf.policy_key(account)) == "1"
    account.unique_id = first
    assert bnf.enabled(service, account) is True


@pytest.mark.parametrize("rebound", ["padded", "literal"])
def test_numilog_old_on_cannot_be_adopted_by_rebound_borrower(
    tmp_path, rebound,
):
    service, account, _session, calls, broker, source_id = _setup(tmp_path)
    try:
        original = account.unique_id
        bound = _Binding(
            "numilog", account.identity_id, account.credential_id, original,
        )
        assert _valid_binding(broker, bound, source_id)
        if rebound == "padded":
            account.unique_id = " " + original + " "
            assert broker._source_id(account) == source_id
        else:
            account.unique_id = (
                "numilog:" + account.credential_id + ":primary"
            )
            source_id = broker._source_id(account)
        changed = _Binding(
            "numilog", account.identity_id, account.credential_id,
            account.unique_id,
        )
        assert not _valid_binding(broker, changed, source_id)
        result = prepare(broker, source_id)
        assert not result.ok and result.facts["status"] == "source_not_ready"
        assert calls == []
    finally:
        service.close()
