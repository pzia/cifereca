"""#5055: exact source-account fence is independent of a provider name."""
from copy import copy
from types import SimpleNamespace as NS

from cifereca.core.source_account_binding import source_account_binding_ready
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core  # noqa: F401


def test_future_provider_requires_same_sql_account_and_live_session(connected_core):
    _service, _session, original, _loan, _state, _downloaded = connected_core
    account = copy(original)
    account.provider_id = "provider.future"
    session = NS(connected=True, accounts=[account])
    calls = []
    class Storage:
        def identity_for_connection(self, provider, credential):
            calls.append((provider, credential))
            return account.identity_id
        def provider_accounts(self, *, identity_id):
            assert identity_id == account.identity_id
            return [dict(provider_id=account.provider_id,
                         credential_id=account.credential_id,
                         account_id=account.unique_id)]
    service = NS(
        accounts=[account], storage=Storage(),
        sync_source_enabled=lambda a: a is account,
        identity_by_id=lambda identity: NS(identity_id=identity),
        _session_key=lambda p, c: (p, c),
        _sessions={(account.provider_id, account.credential_id): session},
    )
    assert source_account_binding_ready(
        service, account, expected_provider_id="provider.future",
    )
    assert calls == [("provider.future", account.credential_id)]
    calls.clear()
    assert not source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    assert calls == []
    session.connected = False
    assert not source_account_binding_ready(
        service, account, expected_provider_id="provider.future",
    )


def test_rebound_provider_account_refuses_even_if_session_still_connected(connected_core):
    _service, _session, account, _loan, _state, _downloaded = connected_core
    session = NS(connected=True, accounts=[account])
    class Storage:
        def identity_for_connection(self, _provider, _credential):
            return account.identity_id
        def provider_accounts(self, *, identity_id):
            return [dict(provider_id="bnf-pret", credential_id=account.credential_id,
                         account_id="different-borrower")]
    service = NS(
        accounts=[account], storage=Storage(),
        sync_source_enabled=lambda a: True,
        identity_by_id=lambda identity: NS(identity_id=identity),
        _session_key=lambda p, c: (p, c),
        _sessions={(account.provider_id, account.credential_id): session},
    )
    assert not source_account_binding_ready(
        service, account, expected_provider_id=account.provider_id,
    )


def test_live_session_getter_is_never_read_at_binding_boundary(
    connected_core, monkeypatch,
):
    """An untrusted plugin property may not execute while proving identity."""
    service, session, account, _loan, _state, downloaded = connected_core
    assert source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    reads = []

    def hostile(_session):
        reads.append("untrusted-provider-getter")
        raise RuntimeError("private provider side effect")

    monkeypatch.setattr(type(session), "accounts", property(hostile))
    assert not source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    assert reads == []
    assert downloaded == []


def test_live_session_scalar_property_cannot_forge_connected(
    connected_core, monkeypatch,
):
    service, session, account, _loan, _state, downloaded = connected_core
    reads = []

    def hostile(_session):
        reads.append("unexpected")
        return True

    monkeypatch.setattr(type(session), "connected", property(hostile))
    assert not source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    assert reads == [] and downloaded == []
