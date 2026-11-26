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
    "account", "binding", "connection_aba", "source_revision", "raw_eviction", "c_epoch",
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
        elif change == "raw_eviction":
            from cifereca.core.notice_cover_repository import _opaque_key
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(source.document),))
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


@pytest.mark.parametrize("generation", [0, "ab0123456789" * 2])
def test_inert_session_generation_may_be_counter_or_opaque_uuid(tmp_path, generation):
    with closing(Storage(tmp_path / "generation.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        session = host._sessions[(a.provider_id, a.credential_id)]
        session._epoch = generation
        source, c = host.source(a)
        menu = host.menu(c, source.document)
        choice, = menu.choices
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) == URL
        session._epoch = str(generation) if type(generation) is int else generation + "-new"
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) is None


def test_qualified_private_eviction_restart_and_fresh_reference_revision(tmp_path):
    path = tmp_path / "retained.sqlite"
    with closing(Storage(path)) as db:
        host = Host(db)
        a = host.account("alice")
        source, c = host.source(a)
        menu = host.menu(c, source.document)
        choice, = menu.choices
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) == URL
        assert read_public_web_choices(db, c.document).choices == ()
    with closing(Storage(path)) as db:
        # New Core/session objects, same durable owner and connection epoch.
        host = Host(db)
        a = Account(identity_id="alice", provider_id="fixture-a",
                    credential_id="card-alice", unique_id="reader-alice",
                    is_primary=True)
        host.accounts = [a]
        host._sessions[host._session_key(a.provider_id, a.credential_id)] = Offline(a)
        menu = host.menu(c, source.document)
        assert resolve_authenticated_web_choice(host, menu, choice_id=menu.choices[0].choice_id) == URL
        updated, revised = host.source(a, url=HOST + "/resources/revised", at=12)
        assert updated.epoch == source.epoch + 2
        assert updated.attribute.state == "pending"
        assert revised.epoch == c.epoch + 1
        assert resolve_authenticated_web_choice(host, menu, choice_id=menu.choices[0].choice_id) is None
        fresh = host.menu(revised, updated.document)
        assert resolve_authenticated_web_choice(host, fresh, choice_id=fresh.choices[0].choice_id) == HOST + "/resources/revised"
        assert evict_notice_cover(db, updated.document, expected_epoch=updated.epoch)
        assert resolve_authenticated_web_choice(host, fresh, choice_id=fresh.choices[0].choice_id) == HOST + "/resources/revised"
        # Rebinding an old durable source must never reauthorize the reference.
        db.provider_connection_put({"identity_id": "alice", "provider_id": a.provider_id,
                                    "credential_id": a.credential_id, "enabled": False})
        db.provider_connection_put({"identity_id": "alice", "provider_id": a.provider_id,
                                    "credential_id": a.credential_id, "enabled": True})
        stale = host.menu(revised, updated.document)
        assert stale is None or stale.choices == ()
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("damage", ["url", "scope", "proof", "epoch", "raw_delete", "sidecar_delete"])
def test_retained_private_reference_rejects_bad_scope_digest_and_fence(tmp_path, damage):
    import json
    from cifereca.core.notice_c_authenticated_web_retention import _key
    from cifereca.core.notice_cover_repository import _opaque_key, _fence_key
    with closing(Storage(tmp_path / "damage.sqlite")) as db:
        host = Host(db)
        a = host.account("alice")
        source, c = host.source(a)
        menu = host.menu(c, source.document)
        if damage != "raw_delete":
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        with db._lock, db._connect() as sql:
            key = _key(source.document)
            if damage == "raw_delete":
                sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(source.document),))
            elif damage == "sidecar_delete":
                sql.execute("DELETE FROM metadata WHERE key=?", (key,))
            elif damage == "epoch":
                key = _fence_key(_opaque_key(source.document))
                raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
                raw["epoch"] += 1
                sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
            else:
                raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
                if damage == "url":
                    raw["field"]["links"][0]["url"] = HOST + "/resources/forged"
                else:
                    raw[damage] = "0" * 64
                sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
        assert resolve_authenticated_web_choice(host, menu, choice_id=menu.choices[0].choice_id) is None
        assert not host.menu(c, source.document).choices


def test_foreign_opaque_source_digest_cannot_be_rebound_to_own_scope(tmp_path):
    with closing(Storage(tmp_path / "foreign.sqlite")) as db:
        host = Host(db)
        alice = host.account("alice")
        bob = host.account("bob")
        foreign, c = host.source(bob)
        assert evict_notice_cover(db, foreign.document, expected_epoch=foreign.epoch)
        forged = replace(foreign.document, identity_id="alice",
                         credential_id=alice.credential_id, account_id=alice.unique_id)
        menu = host.menu(c, forged)
        assert menu is None or menu.choices == ()
        assert host.menu(c, foreign.document, owner="bob").choices


def test_malformed_durable_private_c_cannot_open_saved_choice_or_menu(tmp_path):
    from cifereca.core.notice_c_cover_index import _db_key
    with closing(Storage(tmp_path / "corrupt-private-menu.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        source, canonical = host.source(account)
        menu = host.menu(canonical, source.document)
        choice, = menu.choices
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) == URL
        # Actual SQLite corruption after a valid menu was built. The stored
        # source, account and session are intact; only canonical C is invalid.
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                ('{"schema":true,"sources":{}}', _db_key(canonical.document)),
            )
        assert host.menu(canonical, source.document) is None
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) is None
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("error", [
    "sqlite", "value", "type", "key", "attribute",
])
def test_private_menu_and_click_contain_local_read_failure(tmp_path, monkeypatch, error):
    from sqlite3 import OperationalError
    from cifereca.core import notice_c_authenticated_web_choices as private_web
    errors = {
        "sqlite": OperationalError, "value": ValueError, "type": TypeError,
        "key": KeyError, "attribute": AttributeError,
    }
    with closing(Storage(tmp_path / "fault-private-menu.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        source, canonical = host.source(account)
        menu = host.menu(canonical, source.document)
        choice, = menu.choices

        def unavailable(*_args):
            raise errors[error]("synthetic read failure")

        with monkeypatch.context() as patch:
            patch.setattr(private_web, "_read", unavailable)
            assert host.menu(canonical, source.document) is None
            assert resolve_authenticated_web_choice(
                host, menu, choice_id=choice.choice_id,
            ) is None

        # The original receipt still works after a transient local failure;
        # neither the read nor the click used a provider/network operation.
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) == URL
        assert db.entitlements() == db.acquisitions() == db.placements() == []



@pytest.mark.parametrize("damage", [
    "choices_list", "choices_foreign", "choices_oversized", "choice_id",
    "choice_label", "choice_access_mode", "bindings_list",
    "bindings_foreign", "scope_foreign", "document_foreign",
])
def test_modified_private_click_receipt_never_runs_hostile_equality_or_scope(
    tmp_path, damage,
):
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("foreign equality in private click")

        def __ne__(self, other):
            raise AssertionError("foreign inequality in private click")

        def current(self, *_args):
            raise AssertionError("foreign scope callback in private click")

    with closing(Storage(tmp_path / "hostile-click.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        source, canonical = host.source(account)
        menu = host.menu(canonical, source.document)
        original, = menu.choices
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=original.choice_id,
        ) == URL
        if damage == "choices_list":
            changed = replace(menu, choices=[original])
        elif damage == "choices_foreign":
            changed = replace(menu, choices=(Hostile(),))
        elif damage == "choices_oversized":
            changed = replace(menu, choices=(original,) * 257)
        elif damage == "choice_id":
            changed = replace(menu, choices=(replace(original, choice_id=Hostile()),))
        elif damage == "choice_label":
            changed = replace(menu, choices=(replace(original, label=Hostile()),))
        elif damage == "choice_access_mode":
            changed = replace(menu, choices=(replace(original, access_mode=Hostile()),))
        elif damage == "bindings_list":
            changed = replace(menu, _bindings=list(menu._bindings))
        elif damage == "bindings_foreign":
            changed = replace(menu, _bindings=(Hostile(),))
        elif damage == "scope_foreign":
            changed = replace(menu, _scope=Hostile())
        else:
            changed = replace(menu, document=Hostile())
        assert resolve_authenticated_web_choice(
            host, changed, choice_id=original.choice_id,
        ) is None
        # Rejecting a manipulated copy must not consume or revoke the
        # original live choice for the same exact private source.
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=original.choice_id,
        ) == URL
        assert db.entitlements() == db.acquisitions() == db.placements() == []



@pytest.mark.parametrize("field", ["source_key", "provider_id", "kind", "artifact_id"])
def test_private_source_documents_reject_mutated_scalar_before_hash_or_click(
    tmp_path, field,
):
    class Hostile:
        def __hash__(self):
            raise AssertionError("foreign DocumentRef hash executed")

        def __eq__(self, other):
            raise AssertionError("foreign DocumentRef equality executed")

    with closing(Storage(tmp_path / "scope-hash.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        source, canonical = host.source(account)
        menu = host.menu(canonical, source.document)
        choice, = menu.choices
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) == URL
        mutated = replace(source.document)
        object.__setattr__(mutated, field, Hostile())
        assert read_authenticated_web_choices(
            host, canonical.document,
            reader_scope=capture_search_read_scope(host, "alice"),
            source_documents=(mutated,),
        ) is None
        changed = replace(menu, _bindings=(
            replace(menu._bindings[0], document=mutated),
        ))
        assert resolve_authenticated_web_choice(
            host, changed, choice_id=choice.choice_id,
        ) is None
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) == URL
        assert db.entitlements() == db.acquisitions() == db.placements() == []



@pytest.mark.parametrize("change", [
    "canonical_kind", "canonical_source_key", "canonical_provider_id",
    "lease_identity", "lease_epoch", "lease_service", "lease_control",
])
def test_private_menu_and_click_reject_tampered_canonical_or_reader_scope(
    tmp_path, change,
):
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("untrusted equality in private C lease")

        def __hash__(self):
            raise AssertionError("untrusted hash in private C lease")

    with closing(Storage(tmp_path / "altered-lease.sqlite")) as db:
        host = Host(db)
        account = host.account("alice")
        source, canonical = host.source(account)
        scope = capture_search_read_scope(host, "alice")
        menu = read_authenticated_web_choices(
            host, canonical.document, reader_scope=scope,
            source_documents=(source.document,),
        )
        choice, = menu.choices
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) == URL
        if change.startswith("canonical_"):
            changed_document = replace(canonical.document)
            field_name = change.removeprefix("canonical_")
            object.__setattr__(changed_document, field_name, Hostile())
            assert read_authenticated_web_choices(
                host, changed_document, reader_scope=scope,
                source_documents=(source.document,),
            ) is None
            changed = replace(menu, document=changed_document)
        else:
            if change == "lease_identity":
                altered_scope = replace(scope, identity_id=Hostile())
            elif change == "lease_epoch":
                altered_scope = replace(scope, source_epoch=Hostile())
            elif change == "lease_service":
                altered_scope = replace(scope, service=Hostile())
            else:
                altered_scope = replace(scope, identity_id="alice\x00alternate")
            assert read_authenticated_web_choices(
                host, canonical.document, reader_scope=altered_scope,
                source_documents=(source.document,),
            ) is None
            changed = replace(menu, _scope=altered_scope)
        assert resolve_authenticated_web_choice(
            host, changed, choice_id=choice.choice_id,
        ) is None
        assert resolve_authenticated_web_choice(
            host, menu, choice_id=choice.choice_id,
        ) == URL
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_native_multi_account_search_keeps_exact_web_links_separate(tmp_path):
    from cifereca.models import SearchResult
    from cifereca.core.notice_c_private_search_web import (
        capture_authenticated_search_call, stamp_authenticated_search_rows,
        persist_authenticated_search_rows, read_authenticated_web_for_search,
    )
    with closing(Storage(tmp_path / "multi-account.sqlite")) as db:
        host = Host(db)
        first = host.account("alice")
        second = replace(first, unique_id="reader-second", is_primary=False)
        host.accounts.append(second)
        session = host._sessions[(first.provider_id, first.credential_id)]
        session._accounts.append(second)
        connection, = db.provider_connections(identity_id="alice")
        db.provider_account_put({
            "identity_id": "alice", "provider_id": first.provider_id,
            "credential_id": first.credential_id, "connection_id": connection["connection_id"],
            "account_id": second.unique_id, "is_primary": False,
        })
        def native(account, suffix):
            return SearchResult(
                id="record-" + suffix, record_id="same-record", docbase="fixture",
                medium="digital", title="Livre", author=None, type=None, format=None,
                publisher=None, published_at=None, description=None, thumbnail_url=None,
                friendly_url=HOST + "/resources/" + suffix, scenario="fixture",
                source_provider=account.provider_id, source_credential=account.credential_id,
                source_account_id=account.unique_id, metadata={"authenticated_web_evidence": {
                    "schema": 1, "provider_id": account.provider_id, "record_id": "same-record",
                    "docbase": "fixture", "medium": "digital", "source_revision": None,
                    "scenario": "fixture", "access_mode": "authenticated",
                    "evidence": account.provider_id + ".authenticated_search_record_identifier",
                    "source_field": "Search.record",
                }},
            )
        call = capture_authenticated_search_call(host, session, "alice")
        assert call is not None
        a, b = native(first, "first"), native(second, "second")
        unknown = replace(a, source_account_id="unregistered")
        rows = [a, b, unknown]
        stamp_authenticated_search_rows(host, call, rows)
        assert persist_authenticated_search_rows(host, rows, identity_id="alice") == 2
        scope = capture_search_read_scope(host, "alice")
        menus = [read_authenticated_web_for_search(host, row, reader_scope=scope) for row in (a, b)]
        assert menus[0].document != menus[1].document
        for row, menu in zip((a, b), menus):
            choice, = menu.choices
            assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) == row.friendly_url
        assert read_authenticated_web_for_search(host, unknown, reader_scope=scope) is None

        # Exercise the actual Search entry point on a C whose second source
        # was explicitly related by the existing writer. The relation is
        # controlled here, not inferred from equal provider record IDs.
        from cifereca.core.notice_c_private_search_web import _source_key
        from cifereca.core.notice_cover_projection import notice_cover_document_ref
        from cifereca.core.notice_cover_repository import read_notice_cover
        first_key = _source_key(a, "alice")
        second_key = _source_key(b, "alice")
        second_source = read_notice_cover(db, notice_cover_document_ref(second_key))
        combined = persist_consolidated_cover_source(
            db, canonical_edition_id=first_key.edition_id, source=second_source,
            relation_proof_id="strong:controlled-test",
        )
        menu = read_authenticated_web_for_search(host, a, reader_scope=scope)
        assert menu.document == combined.document and menu.epoch == combined.epoch
        assert {resolve_authenticated_web_choice(host, menu, choice_id=c.choice_id)
                for c in menu.choices} == {a.friendly_url, b.friendly_url}
        assert db.entitlements() == [] and db.acquisitions() == []


def test_exact_mixed_c_web_references_keep_public_and_private_authority_separate(tmp_path):
    """A strong controlled document relation does not grant another account's link.

    This exercises the two existing Core readers, not admission of a mixed
    public Search group or proof of a relation from actual suppliers.
    """
    from cifereca.core.notice_c_web_choices import resolve_public_web_choice
    from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish

    filename = tmp_path / "mixed-web.sqlite"
    public_url = "https://books.example.org/title/public"
    with closing(Storage(filename)) as db:
        host = Host(db)
        owner = host.account("alice")
        host.account("bob")
        public = source(db, "seller-a", "public-record", [link("seller-a", public_url)])
        target = public.observation.key.edition_id
        publish(db, target, public)
        private, combined = host.source(owner, group=target)
        public_menu = read_public_web_choices(db, combined.document)
        private_menu = host.menu(combined, private.document)
        public_choice, = public_menu.choices
        private_choice, = private_menu.choices
        assert public_menu.document == private_menu.document == combined.document
        assert public_menu.epoch == private_menu.epoch == combined.epoch
        assert private_choice.access_mode == "authenticated"
        assert resolve_public_web_choice(
            db, combined.document, expected_epoch=public_menu.epoch,
            choice_id=public_choice.choice_id,
        ) == public_url
        assert resolve_authenticated_web_choice(
            host, private_menu, choice_id=private_choice.choice_id,
        ) == URL
        assert host.menu(combined, private.document, owner="bob") is None
        assert resolve_public_web_choice(
            db, combined.document, expected_epoch=public_menu.epoch,
            choice_id=private_choice.choice_id,
        ) is None
        assert resolve_authenticated_web_choice(
            host, private_menu, choice_id=public_choice.choice_id,
        ) is None
        # Disconnect just the private session without modifying the C.
        host._sessions[host._session_key(owner.provider_id, owner.credential_id)].connected = False
        assert resolve_authenticated_web_choice(
            host, private_menu, choice_id=private_choice.choice_id,
        ) is None
        assert resolve_public_web_choice(
            db, combined.document, expected_epoch=public_menu.epoch,
            choice_id=public_choice.choice_id,
        ) == public_url
        assert evict_notice_cover(db, public.document, expected_epoch=public.epoch)
    with closing(Storage(filename)) as db:
        assert read_public_web_choices(db, combined.document) == public_menu
        assert resolve_public_web_choice(
            db, combined.document, expected_epoch=public_menu.epoch,
            choice_id=public_choice.choice_id,
        ) == public_url
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_native_c_inventory_uses_individual_scopes_after_eviction_and_restart(tmp_path):
    from cifereca.core.notice_c_authenticated_web_choices import (
        read_authenticated_web_choices_for_c,
    )
    filename = tmp_path / "native-inventory.sqlite"
    with closing(Storage(filename)) as db:
        host = Host(db)
        a = host.account("alice")
        second = host.account("alice", provider="fixture-b")
        b = host.account("bob")
        first, _ = host.source(a, record="first")
        other, _ = host.source(second, record="second", url=HOST + "/resources/second")
        foreign, c = host.source(b, record="foreign", url=HOST + "/resources/foreign")
        assert evict_notice_cover(db, other.document, expected_epoch=other.epoch)
    with closing(Storage(filename)) as db:
        host.storage = db
        alice = capture_search_read_scope(host, "alice")
        bob = capture_search_read_scope(host, "bob")
        menu = read_authenticated_web_choices_for_c(host, c.document, reader_scope=alice)
        assert len(menu.choices) == 2 and menu.epoch == c.epoch
        assert {resolve_authenticated_web_choice(host, menu, choice_id=x.choice_id)
                for x in menu.choices} == {URL, HOST + "/resources/second"}
        foreign_menu = read_authenticated_web_choices_for_c(host, c.document, reader_scope=bob)
        choice, = foreign_menu.choices
        assert resolve_authenticated_web_choice(host, foreign_menu, choice_id=choice.choice_id) == (
            HOST + "/resources/foreign")
        assert resolve_authenticated_web_choice(host, menu, choice_id=choice.choice_id) is None
        assert read_authenticated_web_choices_for_c(
            host, c.document, reader_scope=alice, required_source=foreign.document,
        ) is None
        host._sessions[(second.provider_id, second.credential_id)].connected = False
        assert all(resolve_authenticated_web_choice(host, menu, choice_id=x.choice_id) is None
                   for x in menu.choices)
        renewed = read_authenticated_web_choices_for_c(host, c.document, reader_scope=alice)
        remaining, = renewed.choices
        assert resolve_authenticated_web_choice(host, renewed, choice_id=remaining.choice_id) == URL
        assert resolve_authenticated_web_choice(host, foreign_menu, choice_id=choice.choice_id) == (
            HOST + "/resources/foreign")
        assert db.entitlements() == db.acquisitions() == db.placements() == []
