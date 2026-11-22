"""#6397: same provider account identifier does not imply shared authority."""
from contextlib import closing

from cifereca.models import Account
from cifereca.storage import Storage
from cifereca.core.notice_c_authenticated_web_choices import resolve_authenticated_web_choice
from tests.test_core_p0_6397_authenticated_web_choices import Host, Offline, HOST, URL


class AliasHost(Host):
    def account(self, owner, provider="fixture-a", credential=None, account_id=None):
        account = Account(identity_id=owner, provider_id=provider,
                          credential_id=credential or "card-" + owner,
                          unique_id=account_id or "reader-" + owner, is_primary=True, display_name="Fixture")
        self.accounts.append(account)
        self._sessions[self._session_key(provider, account.credential_id)] = Offline(account)
        connection = self.storage.provider_connection_put({
            "identity_id": owner, "provider_id": provider,
            "credential_id": account.credential_id, "enabled": True,
        })
        self.storage.provider_account_put({
            "identity_id": owner, "provider_id": provider,
            "credential_id": account.credential_id, "connection_id": connection,
            "account_id": account.unique_id, "is_primary": True,
        })
        return account


def test_same_account_identifier_under_two_credentials_never_grants_cross_scope(tmp_path):
    """Provider account ID equality alone is not an attested shared account."""
    with closing(Storage(tmp_path / "account-alias.sqlite")) as db:
        host = AliasHost(db)
        a = host.account("alice", account_id="same-upstream-id")
        b = host.account("bob", account_id="same-upstream-id")
        one, _ = host.source(a)
        two, c = host.source(b, url=HOST + "/resources/bob")
        assert one.document != two.document
        alice = host.menu(c, one.document, two.document, owner="alice")
        bob = host.menu(c, one.document, two.document, owner="bob")
        a_choice, = alice.choices
        b_choice, = bob.choices
        assert a_choice.choice_id != b_choice.choice_id
        assert resolve_authenticated_web_choice(host, alice, choice_id=a_choice.choice_id) == URL
        assert resolve_authenticated_web_choice(host, bob, choice_id=b_choice.choice_id) == HOST + "/resources/bob"
        assert resolve_authenticated_web_choice(host, alice, choice_id=b_choice.choice_id) is None
        assert resolve_authenticated_web_choice(host, bob, choice_id=a_choice.choice_id) is None
        host._sessions[host._session_key(a.provider_id, a.credential_id)].connected = False
        assert resolve_authenticated_web_choice(host, alice, choice_id=a_choice.choice_id) is None
        assert resolve_authenticated_web_choice(host, bob, choice_id=b_choice.choice_id) == HOST + "/resources/bob"
        assert db.entitlements() == db.acquisitions() == db.placements() == []

