"""Controlled private observation / C membership / exact click, no provider I/O."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_authenticated_web_choices import (
    FIELD, read_authenticated_web_choices, resolve_authenticated_web_choice,
)
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_web_choices import read_public_web_choices
from cifereca.core.notice_cover_repository import evict_notice_cover, persist_notice_cover
from cifereca.core.notice_source_observation import NoticeSourceKey, admit_source_notice
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.models import Account
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.storage import Storage

EDITION = "edition:authenticated-fixture"
HOST = "https://catalogue.example.org"
URL = HOST + "/resources/book-a"


class Offline(ProviderSession):
    def __init__(self, account):
        super().__init__(
            descriptor=ProviderDescriptor(
                id=account.provider_id, display_name="Fixture", description="Fixture",
                state="ready", capabilities=ProviderCapabilities(authentication=True, search=True),
                homepage=HOST,
            ), credential_id=account.credential_id, label="Fixture",
            credentials={}, options={},
        )
        self._accounts = [account]
        self.connected = True
        self._epoch = 1
        self._closed = False

    def connect(self):
        pytest.fail("no authentication from web choices")

    def close(self):
        self.connected = False

    def search(self, *args, **kwargs):
        pytest.fail("no provider search from web choices")

    def holdings(self, *args, **kwargs):
        pytest.fail("no provider detail from web choices")


class Host:
    def __init__(self, db):
        self.storage = db
        self._source_context_epoch = 1
        self.identities = {name: NS(identity_id=name) for name in ("alice", "bob")}
        self.accounts = []
        self._sessions = {}
        self.enabled = True

    @property
    def current_identity(self):
        pytest.fail("Headless worker must not consult navigation")

    def identity_by_id(self, identity_id):
        return self.identities.get(identity_id)

    def accounts_for_identity(self, identity_id):
        return [a for a in self.accounts if a.identity_id == identity_id]

    def _provider_enabled_for_identity(self, provider_id, identity_id):
        return self.enabled

    @staticmethod
    def _session_key(p, c):
        return p, c

    def account(self, owner, provider="fixture-a", credential=None):
        account = Account(identity_id=owner, provider_id=provider,
                          credential_id=credential or "card-" + owner,
                          unique_id="reader-" + owner, is_primary=True, display_name="Fixture")
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

    def source(self, account, *, url=URL, evidence=None, at=10,
               parser="core.search.authenticated-web", record="record-a",
               connection_epoch=None, links=None, group=EDITION):
        db = self.storage
        epoch = db.provider_connection_epoch(
            account.provider_id, account.credential_id, account.identity_id)
        key = NoticeSourceKey(
            provider_id=account.provider_id, visibility="private",
            identity_id=account.identity_id, credential_id=account.credential_id,
            account_id=account.unique_id, branch_id="search.authenticated.catalogue",
            record_id=record, edition_id="edition:" + record, manifestation_role="digital",
        )
        observation = admit_source_notice(
            key=key, parser_id=parser, parser_version="1", observed_at=at,
            raw_fields={"cover": None, FIELD: {
                "schema": 1,
                "connection_epoch": epoch if connection_epoch is None else connection_epoch,
                "links": links if links is not None else [{
                    "url": url, "evidence": evidence or
                    account.provider_id + ".authenticated_search_record_identifier",
                }],
            }},
        )
        source = persist_notice_cover(db, observation, field_path=("cover",),
                                      cover_inventory_complete=False)
        canonical = persist_consolidated_cover_source(
            db, canonical_edition_id=group, source=source,
            relation_proof_id="strong:controlled-test",
        )
        return source, canonical

    def menu(self, c, *docs, owner="alice"):
        return read_authenticated_web_choices(
            self, c.document, reader_scope=capture_search_read_scope(self, owner),
            source_documents=tuple(docs),
        )


def test_same_c_two_readers_and_multiple_choices_never_exposes_private_url(tmp_path):
    with closing(Storage(tmp_path / "scope.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        b = host.account("bob")
        second = host.account("alice", provider="fixture-b")
        one, _ = host.source(a)
        foreign, _ = host.source(b, url=HOST + "/resources/bob")
        two, c = host.source(second, url=HOST + "/resources/second")
        menu = host.menu(c, one.document, foreign.document, two.document)
        assert menu.epoch == c.epoch and len(menu.choices) == 2
        assert all(row.access_mode == "authenticated" and
                   "Connexion requise" in row.label for row in menu.choices)
        actual = {resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id)
                  for choice in menu.choices}
        assert actual == {URL, HOST + "/resources/second"}
        assert host.menu(c, one.document, owner="bob") is None
        bob = host.menu(c, foreign.document, one.document, owner="bob")
        choice, = bob.choices
        assert resolve_authenticated_web_choice(host, bob, choice_id=choice.choice_id) == (
            HOST + "/resources/bob")
        assert resolve_authenticated_web_choice(host, bob, choice_id=menu.choices[0].choice_id) is None
        assert read_public_web_choices(db, c.document).choices == ()
        assert not c.catalogue_web_references
        with db._lock, db._connect() as sql:
            raw, = sql.execute("SELECT value FROM metadata WHERE key LIKE "
                               "'notice.c.cover.canonical.v1:%'").fetchone()
        assert HOST not in raw and "card-alice" not in raw and "reader-alice" not in raw
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("changes", [
    {"url": HOST + "/search"}, {"url": HOST + "/download/book"},
    {"url": HOST + "/books/book.epub"}, {"url": "https://foreign.example.org/book"},
    {"url": HOST + "/resources/book?lang=fr"}, {"url": HOST + "/resources/%62ook"},
    {"evidence": "another.authenticated_search_record_identifier"},
    {"parser": "legacy"}, {"connection_epoch": True}, {"connection_epoch": 999},
])
def test_unqualified_source_does_not_publish_choice(tmp_path, changes):
    with closing(Storage(tmp_path / "negative.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        source, c = host.source(a, **changes)
        assert not host.menu(c, source.document).choices


@pytest.mark.parametrize("change", [
    "source_epoch", "session_epoch", "session", "disconnect", "plugin",
    "account", "binding", "connection_aba", "source_revision", "eviction", "c_epoch",
])
def test_choice_click_revalidates_exact_scopes(tmp_path, change):
    with closing(Storage(tmp_path / "stale.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        source, c = host.source(a)
        menu = host.menu(c, source.document)
        choice, = menu.choices
        session = host._sessions[(a.provider_id, a.credential_id)]
        if change == "source_epoch":
            host._source_context_epoch += 2
        elif change == "session_epoch":
            session._epoch += 1
        elif change == "session":
            host._sessions[(a.provider_id, a.credential_id)] = Offline(a)
        elif change == "disconnect":
            session.connected = False
        elif change == "plugin":
            host.enabled = False
        elif change == "account":
            host.accounts.append(replace(a))
        elif change in ("binding", "connection_aba"):
            db.provider_connection_put({
                "identity_id": "alice", "provider_id": a.provider_id,
                "credential_id": a.credential_id, "enabled": False,
            })
            if change == "connection_aba":
                db.provider_connection_put({
                    "identity_id": "alice", "provider_id": a.provider_id,
                    "credential_id": a.credential_id, "enabled": True,
                })
        elif change == "source_revision":
            replacement = admit_source_notice(
                key=source.observation.key, parser_id=source.observation.parser_id,
                parser_version="1", observed_at=11, raw_fields={"cover": None},
            )
            persist_notice_cover(db, replacement, field_path=("cover",),
                                 cover_inventory_complete=False)
        elif change == "eviction":
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        else:
            host.source(a, url=HOST + "/resources/revised", at=11)
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) is None
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_unrelated_cannot_read_source_even_with_valid_account(tmp_path):
    with closing(Storage(tmp_path / "unrelated.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        first, _ = host.source(a)
        _, other = host.source(a, record="different", group="edition:other")
        assert host.menu(other, first.document).choices == ()


@pytest.mark.parametrize("url", [HOST + "/resources/book#section", HOST + "/resources/book?token=unsafe"])
def test_unsafe_url_is_refused_by_admission_before_any_c_write(tmp_path, url):
    with closing(Storage(tmp_path / "admission.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        with pytest.raises(ValueError, match="invalid_source_notice_observation"):
            host.source(account, url=url)
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
        assert db.entitlements() == db.acquisitions() == db.placements() == []
