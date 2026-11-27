"""#6397: independent native SSO, private principal equality, one native C.

No live login, exported subject, cookie reuse, or additional provider GET.
"""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.plugin_api import ProviderConfigurationError
from cifereca.core.notice_c_authenticated_web_choices import resolve_authenticated_web_choice
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_private_search_web import (
    capture_authenticated_search_call, stamp_authenticated_search_rows,
    persist_authenticated_search_rows, read_authenticated_web_for_search,
)
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient, ACTIVITY
from test_http_provider import LOGIN, SAML
from test_p0_6397_authenticated_web_proof import ORIGIN, card, results

ACT = '<section class="active-loans"></section>'
PERSONAL = "reader@example.test"
OTHER = "other@example.test"


def make_session(credential, username, *, mode="full"):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.host, request.url.path))
        if str(request.url) == ACTIVITY:
            if (mode == "direct" or request.headers.get("cookie") == "reader=synthetic"
                    or (mode == "no-saml" and ("POST", "authentification.bnf.fr", "/login") in calls)):
                return httpx.Response(200, text=ACT, headers={"content-type": "text/html"})
            return httpx.Response(302, headers={
                "location": "https://idppub.bnf.fr/idp/profile/SAML2/Redirect/SSO"})
        if request.url.host == "idppub.bnf.fr":
            return httpx.Response(302, headers={
                "location": "https://authentification.bnf.fr/login?service=synthetic"})
        if request.url.host == "authentification.bnf.fr":
            if request.method == "GET":
                text = LOGIN
            else:
                fields = parse_qs(request.content.decode("ascii"))
                assert fields["username"] == [username]
                assert fields["password"] == ["synthetic-password"]
                if mode == "no-saml":
                    return httpx.Response(302, headers={
                        "location": ACTIVITY, "set-cookie": "reader=synthetic; Path=/; Secure"})
                text = LOGIN if mode == "failed" else SAML
            return httpx.Response(200, text=text, headers={"content-type": "text/html"})
        if request.url.path == "/sso/saml/consume":
            assert request.method == "POST"
            return httpx.Response(302, headers={
                "location": ACTIVITY, "set-cookie": "reader=synthetic; Path=/; Secure"})
        assert request.method == "GET" and request.url.path == "/resources"
        assert request.headers.get("cookie") == "reader=synthetic"
        return httpx.Response(200, text=results(card()), headers={"content-type": "text/html"})

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    session = BnfProviderSession(client=client, credential_id=credential, label=credential,
                                 credentials={"username": username, "password": "synthetic-password"},
                                 options={})
    return session, calls


@pytest.mark.parametrize("mode,username", [
    ("direct", PERSONAL), ("no-saml", PERSONAL), ("full", "professional-alias"),
    ("failed", PERSONAL),
])
def test_unqualified_login_is_not_an_account_equivalence(mode, username):
    session, _ = make_session("fixture", username, mode=mode)
    try:
        if mode == "failed":
            with pytest.raises(ProviderConfigurationError):
                session.connect()
        else:
            assert len(session.connect()) == 1
        assert session._authenticated_catalogue_principal_v1 is None
        assert session.client._catalogue_principal is None
    finally:
        session.close()


@pytest.mark.parametrize("third_username", [PERSONAL, OTHER])
def test_same_independently_authenticated_personal_account_retains_exact_authorities(
        tmp_path, monkeypatch, third_username):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [{"id": "alice", "label": "Alice"},
                              {"id": "bob", "label": "Bob"}])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "private.sqlite"), Diagnostics())
    service.accounts = []
    service._sessions = {}
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
    db = service.storage
    sessions, calls, rows = [], [], []

    def observe(owner, credential, username):
        session, log = make_session(credential, username)
        sessions.append(session)
        calls.append(log)
        account, = session.connect()
        account.identity_id = owner
        service.accounts.append(account)
        service._sessions[service._session_key(account.provider_id, account.credential_id)] = session
        connection = db.provider_connection_put({
            "identity_id": owner, "provider_id": account.provider_id,
            "credential_id": account.credential_id, "enabled": True,
        })
        db.provider_account_put({
            "connection_id": connection, "identity_id": owner, "provider_id": account.provider_id,
            "credential_id": account.credential_id, "account_id": account.unique_id,
            "is_primary": True,
        })
        call = capture_authenticated_search_call(service, session, owner)
        assert call is not None
        original = session.search("fiction")
        stamp_authenticated_search_rows(service, call, original)
        assert persist_authenticated_search_rows(service, original, identity_id=owner) == 1
        row, = original
        rows.append(row)
        return session

    def menu(owner, row):
        return read_authenticated_web_for_search(
            service, row, reader_scope=capture_search_read_scope(service, owner),
        )

    try:
        a = observe("alice", "alice-card", PERSONAL)
        b = observe("bob", "bob-card", PERSONAL)
        duplicate = observe("alice", "other-card", third_username)
        assert len({id(s.client.http.cookies) for s in sessions}) == 3
        proofs = [s._authenticated_catalogue_principal_v1 for s in sessions]
        assert all(type(p) is tuple and len(p) == 4 for p in proofs)
        assert proofs[0][3] == proofs[1][3]
        assert (proofs[0][3] == proofs[2][3]) is (third_username == PERSONAL)
        assert proofs[0][2] is not proofs[1][2]
        menu_a, menu_b = menu("alice", rows[0]), menu("bob", rows[1])
        assert menu_a.document == menu_b.document
        assert len(menu_a.choices) == (1 if third_username == PERSONAL else 2)
        assert len(menu_b.choices) == 1
        assert len(menu_a._bindings) == 2 and len(menu_b._bindings) == 1
        assert menu_b._bindings[0].principal == proofs[1][3]
        canonical = read_consolidated_notice_cover(db, menu_a.document)
        assert len(canonical.sources) == 3
        # The independently attested native catalogue bytes are stored
        # ONCE, with three exact source envelopes (not three catalogues).
        # Distinct accounts can observe identical descriptive bytes;
        # that never grants an equivalent account or identical choice.
        from cifereca.core.notice_cover_repository import (
            _SHARED_PREFIX, _opaque_key, read_current_notice_cover,
        )
        from cifereca.core.notice_c_private_search_web import _source_key
        from cifereca.core.notice_cover_projection import notice_cover_document_ref
        from cifereca.core.notice_c_authenticated_web_retention import FIELD
        documents = [notice_cover_document_ref(_source_key(row, owner))
                     for row, owner in zip(rows, ("alice", "bob", "alice"))]
        with db._lock, db._connect() as sql:
            bodies = sql.execute("SELECT value FROM metadata WHERE key LIKE ?",
                                 (_SHARED_PREFIX + "%",)).fetchall()
            envelopes = [json.loads(sql.execute("SELECT value FROM metadata WHERE key=?",
                                                (_opaque_key(doc),)).fetchone()[0])
                         for doc in documents]
        body, = [json.loads(row[0]) for row in bodies]
        assert body["refs"] == sorted(doc.source_key for doc in documents)
        assert FIELD not in json.loads(body["body"])
        assert len({row["documentary_payload"]["digest"] for row in envelopes}) == 1
        assert all("raw_json" not in row for row in envelopes)
        observations = [read_current_notice_cover(db, doc) for doc in documents]
        assert len({obs.observation.source_revision for obs in observations}) == 3
        assert [row["documentary_payload"]["private_web"] for row in envelopes] == [
            obs.observation.fields_copy()[FIELD] for obs in observations]
        assert not {c.choice_id for c in menu_a.choices} & {c.choice_id for c in menu_b.choices}
        assert resolve_authenticated_web_choice(
            service, menu_a, choice_id=menu_b.choices[0].choice_id) is None
        for choice in menu_a.choices:
            assert resolve_authenticated_web_choice(
                service, menu_a, choice_id=choice.choice_id) == ORIGIN + "/resources/record-a"
        assert resolve_authenticated_web_choice(
            service, menu_b, choice_id=menu_b.choices[0].choice_id) == ORIGIN + "/resources/record-a"
        # Equality is never an authorization. Removing either independent
        # declaration invalidates the old menu; Bob remains authorized.
        duplicate.close()
        assert duplicate._authenticated_catalogue_principal_v1 is None
        assert resolve_authenticated_web_choice(
            service, menu_a, choice_id=menu_a.choices[0].choice_id) is None
        current = menu("alice", rows[0])
        assert len(current.choices) == 1
        assert resolve_authenticated_web_choice(
            service, menu_b, choice_id=menu_b.choices[0].choice_id) == ORIGIN + "/resources/record-a"
        # No principal or raw login in Notice C (including any shared body).
        with db._lock, db._connect() as sql:
            persisted = json.dumps(sql.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'notice.c.%'").fetchall())
        assert PERSONAL not in persisted and OTHER not in persisted
        assert all(proof[3].hex() not in persisted for proof in proofs)
        assert all(sum(path == "/resources" for _, _, path in log) == 1 for log in calls)
        assert all(sum(method == "POST" for method, _, _ in log) == 2 for log in calls)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
    finally:
        service.close()
