"""Core Library display reads exact borrower cache, never GTK last_loans."""
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_display_cache import loan_display_rows, _loan_cache_key
from cifereca.models import Account, Loan


class Host:
    def __init__(self):
        self.readers = {key: NS(identity_id=key) for key in ("alice", "bob")}
        self.accounts = [Account(identity_id=key, provider_id="synthetic",
                                 credential_id="card-" + key, unique_id="same-borrower")
                         for key in self.readers]
        self._source_context_epoch = 7
        self.epoch = 3
        self.cache = {}
        self.reads = []
        self._sessions = {}
        for account in self.accounts:
            self._sessions[(account.provider_id, account.credential_id)] = NS(
                provider_id=account.provider_id, credential_id=account.credential_id,
                connected=True, loans=lambda *_: pytest.fail("cache-only projection"),
            )
        self.storage = NS(
            provider_connection_epoch=lambda *_: self.epoch,
            provider_connection_epoch_current=lambda p, c, i, epoch: self.epoch == epoch,
            provider_scope_account_bound=lambda **kw: True,
            cache_get=self.get,
        )

    @property
    def last_loans(self):
        pytest.fail("mutable GTK projection accessed")

    @property
    def current_identity(self):
        pytest.fail("navigation consulted")

    def identity_by_id(self, key):
        return self.readers.get(key)

    def accounts_for_identity(self, key):
        return [row for row in self.accounts if row.identity_id == key]

    @staticmethod
    def _session_key(p, c):
        return p, c

    def _provider_enabled_for_identity(self, provider, reader):
        return True

    def get(self, namespace, key):
        assert namespace == "account"
        self.reads.append(key)
        return self.cache.get(key)

    def seed(self, account, **changes):
        book = Loan(id="same-loan", provider="synthetic", medium="digital",
                    record_id="same-edition", title=account.identity_id,
                    source_provider=account.provider_id, source_credential=account.credential_id,
                    source_account_id=account.unique_id, identity_id=account.identity_id)
        self.cache[_loan_cache_key(account)] = [{**asdict(book), **changes}]


def test_loan_display_independent_clients_same_borrower_and_edition():
    host = Host()
    a, b = host.accounts
    host.seed(a)
    host.seed(b)
    assert [row.title for row in loan_display_rows(host, "alice")] == ["alice"]
    assert [row.title for row in loan_display_rows(host, "bob")] == ["bob"]
    assert host.reads == [_loan_cache_key(a), _loan_cache_key(b)]
    assert loan_display_rows(host, "unknown") == ()
    assert loan_display_rows(host, None) == ()


@pytest.mark.parametrize("changed", ["identity", "credential", "borrower", "epoch",
                                      "source", "registry", "session"])
def test_corrupt_or_revoked_display_cache_is_never_used(changed):
    host = Host()
    a = host.accounts[0]
    if changed == "identity":
        host.seed(a, identity_id="bob")
    elif changed == "credential":
        host.seed(a, source_credential="other")
    elif changed == "borrower":
        host.seed(a, source_account_id="foreign")
    else:
        host.seed(a)
        def during(namespace, key):
            data = host.get(namespace, key)
            if changed == "epoch":
                host.epoch += 2
            elif changed == "registry":
                host.accounts.remove(a)
            elif changed == "session":
                host._sessions.clear()
            else:
                host._source_context_epoch += 1
            return data
        host.storage.cache_get = during
    assert loan_display_rows(host, "alice") == ()


def test_ambiguous_cached_account_is_not_read():
    host = Host()
    a = host.accounts[0]
    host.seed(a)
    host.accounts.append(a)
    assert loan_display_rows(host, "alice") == ()
    assert host.reads == []
