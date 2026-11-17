"""Headless explicit legacy download: no selection, exact source, one attempt."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from tests.test_core_p0_6560_scoped_holdings_read import row


class Host:
    def __init__(self, root):
        self.root = root
        self.identity = NS(identity_id="alice")
        self._source_context_epoch = 5
        self.connection_epoch = 8
        self.owner = "alice"
        self.enabled = True
        self.account = Account(identity_id="alice", provider_id="synthetic",
                               credential_id="card-a", unique_id="account-a")
        self.accounts = [self.account]
        self.session = NS(provider_id="synthetic", credential_id="card-a",
                          connected=True, accounts=[self.account])
        self._sessions = {("synthetic", "card-a"): self.session}
        self.calls = []
        self.on_get = lambda: None
        self.session.download_direct = self.get
        self.acquisition = NS(
            content_type_for=lambda _item: "ebook",
            override_command=lambda _provider: None,
            run_post_download=lambda *_: self.calls.append("post"),
            acquisition_from_direct=lambda item, **kw: NS(
                provider_id=item.source_provider, credential_id=item.source_credential,
                item_id=item.record_id, path=kw["path"],
            ),
        )
        self.storage = NS(
            provider_scope_account_bound=lambda **kw: (
                self.enabled and self.owner == kw["identity_id"]
                and kw["account_id"] == "account-a"
            ),
            provider_connection_epoch=lambda *_: self.connection_epoch,
            provider_connection_epoch_current=lambda p, c, i, epoch: (
                self.enabled and self.owner == i and self.connection_epoch == epoch
            ),
        )
        self.diagnostics = NS(add=lambda *_a, **_kw: None)

    @property
    def current_identity(self):
        pytest.fail("navigation used for confirmed acquisition")

    @property
    def _account_context_epoch(self):
        pytest.fail("navigation epoch used for confirmed acquisition")

    def identity_by_id(self, key):
        return self.identity if key == "alice" else None

    def _provider_enabled_for_identity(self, provider, identity):
        return self.enabled and (provider, identity) == ("synthetic", "alice")

    def can_direct_download_for_scope(self, scope, item):
        return scope.current(self) and self.enabled

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential

    def identity_for_connection(self, *_):
        return NS(identity_id=self.owner)

    def library_identity_provider_dir(self, identity, provider):
        assert (identity, provider) == ("alice", "synthetic")
        return self.root

    def _record_action(self, identity, *_a, **_kw):
        assert identity == "alice"

    def get(self, item, target):
        assert target == self.root
        self.calls.append(("GET", item.title))
        path = self.root / "book.epub"
        path.write_bytes(b"synthetic")
        self.on_get()
        return path, "https://synthetic.invalid/book.epub"

    def catalog_index_path(self, path, **kw):
        assert kw["identity_id"] == "alice"
        self.calls.append("index")
        return {"status": "indexed", "artifact_id": "synthetic"}


def admit(host, item=None, *, account="account-a", confirm=True):
    return LibraryService.capture_confirmed_legacy_search_offer(
        host, item if item is not None else row(),
        identity_id="alice", account_id=account, confirmed=confirm,
    )


def execute(host, target):
    return LibraryService.obtain_admitted_legacy_search_offer(host, target)


def test_exact_admitted_api_detaches_item_and_never_replays(tmp_path):
    host = Host(tmp_path)
    item = row()
    target = admit(host, item)
    assert host.calls == []
    item.title = "changed after admission"
    result = execute(host, target)
    assert result.identity_id == "alice"
    assert host.calls == [("GET", "Synthetic book"), "post", "index"]
    with pytest.raises(ProviderConfigurationError, match="confirmation_consumed"):
        execute(host, target)


@pytest.mark.parametrize("change", ["owner", "epoch", "source", "session", "account", "policy"])
def test_revoke_before_get(tmp_path, change):
    host = Host(tmp_path)
    target = admit(host)
    if change == "owner":
        host.owner = "bob"
    elif change == "epoch":
        host.connection_epoch += 2
    elif change == "source":
        host._source_context_epoch += 1
    elif change == "session":
        host._sessions[("synthetic", "card-a")] = NS()
    elif change == "account":
        host.accounts.append(replace(host.account))
    else:
        host.enabled = False
    with pytest.raises(ProviderConfigurationError, match="source_revoked"):
        execute(host, target)
    assert host.calls == []


def test_durable_aba_during_get_does_not_publish_or_run_hook(tmp_path):
    host = Host(tmp_path)
    target = admit(host)
    host.on_get = lambda: setattr(host, "connection_epoch", 10)
    with pytest.raises(ProviderConfigurationError, match="source_revoked"):
        execute(host, target)
    assert host.calls == [("GET", "Synthetic book")]
    assert (tmp_path / "book.epub").exists()


@pytest.mark.parametrize("reason", ["unconfirmed", "wrong_account", "shared",
                                   "stale", "canonical", "ambiguous"])
def test_reject_ineligible_legacy_action(tmp_path, reason):
    host = Host(tmp_path)
    item = row()
    account, confirm = "account-a", True
    if reason == "unconfirmed":
        confirm = 1
    elif reason == "wrong_account":
        account = "another"
    elif reason == "shared":
        item.metadata = {"catalogue_access": "shared_read_only"}
    elif reason == "stale":
        item.metadata = {"search_provenance": "known_catalog_stale"}
    elif reason == "canonical":
        host.session.selected_purchase_branch = "private"
    else:
        host.accounts.append(replace(host.account, unique_id="other"))
    with pytest.raises(ProviderConfigurationError, match="legacy_direct_admission_unavailable"):
        admit(host, item, account=account, confirm=confirm)
    assert host.calls == []
