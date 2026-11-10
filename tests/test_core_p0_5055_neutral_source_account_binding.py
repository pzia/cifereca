"""#5055: source-account fence with a synthetic provider and no BnF fixtures."""
from types import SimpleNamespace as NS

from cifereca.core.source_account_binding import source_account_binding_ready
from cifereca.models import Account


def _account():
    return Account(identity_id="alice", provider_id="provider.future",
                   credential_id="credential-future", unique_id="borrower-future")


def _service(account, session, *, stored_account_id=None):
    class Storage:
        def identity_for_connection(self, provider, credential):
            assert (provider, credential) == (account.provider_id, account.credential_id)
            return account.identity_id

        def provider_accounts(self, *, identity_id):
            assert identity_id == account.identity_id
            return [dict(provider_id=account.provider_id,
                         credential_id=account.credential_id,
                         account_id=(stored_account_id if stored_account_id is not None
                                     else account.unique_id))]

    return NS(accounts=[account], storage=Storage(),
              sync_source_enabled=lambda a: a is account,
              identity_by_id=lambda i: NS(identity_id=i),
              _session_key=lambda p, c: (p, c),
              _sessions={(account.provider_id, account.credential_id): session})


def test_future_provider_requires_same_sql_account_and_live_session():
    account = _account()
    session = NS(connected=True, accounts=[account])
    service = _service(account, session)
    assert source_account_binding_ready(
        service, account, expected_provider_id="provider.future",
    )
    assert not source_account_binding_ready(
        service, account, expected_provider_id="another-provider",
    )
    session.connected = False
    assert not source_account_binding_ready(
        service, account, expected_provider_id="provider.future",
    )


def test_rebound_provider_account_refuses_even_when_connected():
    account = _account()
    session = NS(connected=True, accounts=[account])
    service = _service(account, session, stored_account_id="different-borrower")
    assert not source_account_binding_ready(
        service, account, expected_provider_id="provider.future",
    )
