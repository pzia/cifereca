"""Real Numilog private/public adapters and Core runtime, no live HTTP.

Only the already-authenticated session and HTTP bodies are synthetic.
No hand-built SearchResult/rights snapshot, no forced journal completion.
"""
import hashlib
import json
from pathlib import Path
from threading import RLock, Event
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest
from contextlib import ExitStack

from cifereca.core.canonical_sync_stage import observe_canonical_rights_in_sync
from cifereca.core.notice_c_purchase_runtime import install_purchase_runtime
from cifereca.core.notice_c_purchase_descriptive_read import read_purchase_descriptive_notice
from cifereca.core.notice_c_purchase_library_projection import (
    _status_txn, project_purchase_library_notices, purchase_library_notice,
    purchase_library_status, purchase_status_label,
)
from cifereca.core.search import filter_loaded_results, local_match_explanation
from cifereca.gui.library_rows import project_library_rows
from cifereca.gui.local_book_detail_sections import local_book_detail_sections
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.plugin_api import Account, from_legacy_rights_snapshot
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from cifereca.task_queue import TaskPriority
from tests.test_core_p0_3171_canonical_sync_stage import Service
from tests.test_core_p0_3171_atomic_publication_v14 import register

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-numilog"
PRODUCT = "https://www.numilog.com/123/ouvrage.ebook"
TITLE = "Titre de recette"
SHARE = "https://www.numilog.com/Pages/Readzis/Recommand.ashx?LivreId=43&amp;SN=FB"


def card(lid, href):
    return (f'<tr><td><a href="{href}"><span class="pTitle">{TITLE}</span></a>'
            f'<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m=dwld">Get</a>'
            '</td></tr>')


@pytest.mark.parametrize("restart_before_worker,heartbeat", (
    (False, False), (True, False), (True, True),
))
def test_real_numilog_adapters_same_read_automatic_c_and_reopen(
        tmp_path, monkeypatch, restart_before_worker, heartbeat):
    monkeypatch.syspath_prepend(str(PLUGIN))
    import cifereca_numilog as private_module
    import cifereca_numilog_public_client as public_client
    from cifereca_numilog_public_provider import NumilogPublicSession

    credential, owner = "fixture-credential", "A"
    account_id = "numilog:" + hashlib.sha256(credential.encode()).hexdigest()[:20]
    inventory = ('<html><body><table id="myTable"><tbody>'
                 + card(42, PRODUCT) + card(43, SHARE) + '</tbody></table></body></html>')
    product = '<script type="application/ld+json">' + json.dumps({
        "@type": "Book", "url": PRODUCT, "name": TITLE,
        "author": [{"name": "Auteur de recette"}],
        "publisher": {"name": "Editeur de recette"},
        "description": "Résumé de recette", "datePublished": "2025-03-15",
        "isbn": "9782702194287", "inLanguage": "fr",
        "isPartOf": {"@type": "BookSeries", "name": "Cycle de recette"},
    }) + '</script>'
    requests = []
    public_entered, public_release = Event(), Event()

    def private_http(request):
        assert request.method == "GET"
        assert request.url.host == "www.numilog.com"
        assert request.url.path.lower() == "/pages/compte/meslivres.aspx"
        requests.append("inventory")
        return httpx.Response(200, text=inventory, request=request)

    def public_http(request):
        assert request.method == "GET" and str(request.url) == PRODUCT
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if heartbeat:
            public_entered.set()
            assert public_release.wait(10), "test did not release blocked public transport"
        requests.append("detail")
        return httpx.Response(200, text=product,
                              headers={"content-type": "text/html; charset=utf-8"},
                              request=request)

    def transport(**kwargs):
        handler = private_http if kwargs.get("producer") == "provider.numilog" else public_http
        assert kwargs.get("follow_redirects") is False
        return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False,
                            headers=kwargs.get("headers"))

    monkeypatch.setattr(private_module.HTTPX_TRANSPORT, "client", transport)
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", transport)
    broker = SimpleNamespace(accounts=lambda identity: [
        SimpleNamespace(provider_id="numilog", account_id=account_id,
                        credential_id=credential, identity_id=owner, scope_version=2)
    ] if identity == owner else [])
    account = Account(identity_id=owner, provider_id="numilog",
                      credential_id=credential, unique_id=account_id)
    path = tmp_path / "reception.sqlite"
    wakes = []
    snapshots = []
    # Each lifecycle owns new sessions, Core service, storage and task broker.
    # The same fixture account is reconnected, never an old session/callback.
    def start_lifecycle(stack, *, initial=False):
        db = Storage(path)
        stack.callback(db.close)
        if initial:
            register(db, owner, "numilog", credential, account=account_id)
        private = private_module.NumilogSession(
            credential_id=credential, label="Fixture", credentials={}, options={},
            config={}, diagnostics=None, identity_broker=broker,
        )
        stack.callback(private.close)
        private.connected = True
        private._accounts = [account]
        public = NumilogPublicSession(credential_id="__public__", label="Public",
                                      credentials={}, options={})
        stack.callback(public.close)
        public.connect()
        service = Service(db, tmp_path / "library")
        service.account = account
        service.accounts = [account]
        service._source_context_lock = RLock()
        service._sessions = {
            service._session_key("numilog", credential): private,
            service._session_key("numilog-catalog", "__public__"): public,
        }
        service._provider_enabled_for_identity = lambda provider, identity: (
            provider == "numilog-catalog" and identity == owner)
        tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                               SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
        stack.callback(tasks.close)
        runtime = NoticeWorkRuntime(db, tasks)
        stack.callback(runtime.close)
        monkeypatch.setattr(runtime, "request_tick", lambda: wakes.append(True))
        executor = install_purchase_runtime(service, runtime)
        return db, service, runtime, executor, tasks

    with ExitStack() as stack:
        db, service, runtime, executor, tasks = start_lifecycle(stack, initial=True)
        result = observe_canonical_rights_in_sync(service, account,
                                                   snapshot_sink=snapshots.append)
        assert result["observed"] == 2 and result["attested_active"] == 2
        assert result["purchase_enrichment"] == {
            "requested": 1, "admitted": 1, "deferred": 0, "unrequested": 0}
        assert requests == ["inventory"] and wakes == [True]
        snapshot, = snapshots
        chosen, omitted = (item.source for item in from_legacy_rights_snapshot(snapshot).resources)
        with db._lock, db._connect() as sql:
            assert _status_txn(sql, omitted, now=executor._now())[0] == "direct_locator_unattested"
        if restart_before_worker:
            assert read_purchase_descriptive_notice(db, chosen, identity_id=owner) is None
            with db._lock, db._connect() as sql:
                assert _status_txn(sql, chosen, now=executor._now()) == ("pending", 0)
            stack.close()
            db, service, runtime, executor, tasks = start_lifecycle(stack)
            # A durable intent is not authority. No fetch or claim before
            # the replacement runtime receives the fresh same-read inventory.
            assert not executor.ready_refs()
            assert not runtime._pump.tick().handles
            assert requests == ["inventory"]
            with db._lock, db._connect() as sql:
                assert _status_txn(sql, chosen, now=executor._now()) == ("pending", 0)
            recovered = observe_canonical_rights_in_sync(service, account)
            assert recovered["purchase_enrichment"]["admitted"] == 1
            assert requests == ["inventory", "inventory"]
            assert executor.ready_refs()
        prefix = ["inventory"] * (2 if restart_before_worker else 1)
        # The real runtime owns both the purchase work and follow-up aggregate.
        # In the additional heartbeat variant use the production admitted
        # dispatcher (the patched sync wake is merely a deterministic hint).
        # Block the actual public adapter response, not the shared broker.
        if heartbeat:
            try:
                scan = NoticeWorkRuntime.request_tick(runtime)
                assert scan is not None
                handles = scan.result(timeout=5).handles
                assert handles and public_entered.wait(5)
                assert requests == prefix
                assert runtime.documentary_revision() >= 1
                assert read_purchase_descriptive_notice(db, chosen, identity_id=owner) is None
                with db._lock, db._connect() as sql:
                    assert _status_txn(sql, chosen, now=executor._now()) == ("running", 1)
                # A user action completes while the actual HTTP mock is blocked.
                # This is capacity evidence, not a GUI latency measurement.
                user = tasks.submit(lambda control: "interactive",
                                    priority=TaskPriority.USER_ACTION)
                assert user.result(timeout=5) == "interactive"
                assert not public_release.is_set()
            finally:
                public_release.set()
            for handle in handles:
                assert handle.result(timeout=5) == "current"

        deadline = monotonic() + 10
        notice = None
        while monotonic() < deadline:
            if heartbeat:
                scan = NoticeWorkRuntime.request_tick(runtime)
                if scan is None:
                    Event().wait(.01)
                    continue
                pending = scan.result(timeout=5).handles
            else:
                pending = runtime._pump.tick().handles
            for handle in pending:
                assert handle.result(timeout=5) == "current"
            notice = read_purchase_descriptive_notice(db, chosen, identity_id=owner)
            if notice is not None:
                break
            Event().wait(.01)
        assert notice is not None
        assert requests == prefix + ["detail"]
        facts = {fact.field: fact.value for fact in notice.facts}
        assert facts["author"] == "Auteur de recette" and facts["publisher"] == "Editeur de recette"
        assert facts["description"] == "Résumé de recette"
        assert facts["languages"] == "fr" and "isbn" not in facts
        assert facts["series"] == "Cycle de recette"
        assert not notice.proves_acquired_edition and not notice.authorizes_acquisition
        assert read_purchase_descriptive_notice(db, omitted, identity_id=owner) is None
        assert read_purchase_descriptive_notice(db, chosen, identity_id="B") is None
        again = observe_canonical_rights_in_sync(service, account)
        assert again["purchase_enrichment"]["admitted"] == 0
        assert requests == prefix + ["detail", "inventory"]
        assert read_purchase_descriptive_notice(db, chosen, identity_id=owner) == notice
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        assert not service.root.exists()
    reopened = Storage(path)
    try:
        assert read_purchase_descriptive_notice(reopened, chosen, identity_id=owner) == notice
        assert read_purchase_descriptive_notice(reopened, omitted, identity_id=owner) is None

        def remote_row(source, key):
            scope = source.scope
            return {"key": key, "identity_id": scope.identity_id,
                    "provider_id": scope.provider_id, "item_id": source.edition_id,
                    "source": "private_catalog_observation", "state": "remote_catalog",
                    "local": False, "title": TITLE, "author": "",
                    "metadata": {}, "possessions": [],
                    "source_refs": [{"identity_id": scope.identity_id,
                                     "provider_id": scope.provider_id,
                                     "credential_id": scope.credential_id,
                                     "account_id": scope.account_id,
                                     "item_id": source.edition_id, "kind": "observation"}]}

        rows = [remote_row(chosen, "direct"), remote_row(omitted, "readzis")]
        foreign = remote_row(chosen, "foreign")
        foreign["identity_id"] = "B"
        rows.append(foreign)
        project_purchase_library_notices(reopened, rows, identity_id=owner)
        direct, missing, outsider = rows
        packet = purchase_library_notice(direct)
        assert packet is not None and packet.notice == notice
        assert purchase_library_status(direct) is None
        assert purchase_library_notice(missing) is None
        status = purchase_library_status(missing)
        assert status is not None and status.state == "direct_locator_unattested"
        assert purchase_library_notice(outsider) is None
        assert purchase_library_status(outsider) is None
        rendered, _ = project_library_rows(
            rows[:2], row_factory=lambda *args: SimpleNamespace(values=args),
            provider_display_name=str, availability_label=lambda _: "",
            backup_status_label=str, format_date=str,
        )
        assert rendered[0].values[2] == "Auteur de recette"
        assert "Auteur : catalogue public" in rendered[0].values[3]
        assert rendered[1].values[2] == ""
        assert purchase_status_label(status) in rendered[1].values[3]
        assert rendered[0]._purchase_library_notice == packet
        assert rendered[1]._purchase_library_status == status

        def sections(item):
            return dict(local_book_detail_sections(
                item, provider_display_name=str, availability_label=lambda _: "",
                identity_by_id=lambda _: None, sync_status_label=str,
                language_label=lambda _: "",
                purchase_notice=purchase_library_notice(item),
                purchase_status=purchase_library_status(item),
            ))

        detail = sections(direct)["Fiche du catalogue public"]
        for label, value in (("Auteur", "Auteur de recette"),
                             ("Éditeur", "Editeur de recette"),
                             ("Résumé", "Résumé de recette"),
                             ("Série", "Cycle de recette"),
                             ("Langues déclarées", "fr")):
            assert (label, value) in detail
        assert ("Portée", "Fiche commerciale, pas une preuve de l’édition acquise") in detail
        assert "Fiche du catalogue public" not in sections(missing)
        assert sections(missing)["Enrichissement documentaire"] == [
            ("État", purchase_status_label(status))]
        assert "Fiche du catalogue public" not in sections(outsider)
        for term, field in (("Auteur de recette", "author"),
                            ("Cycle de recette", "series"),
                            ("Résumé de recette", "all")):
            assert filter_loaded_results(rows, term, field=field) == [direct]
            assert "catalogue public" in local_match_explanation(direct, term)
        # No fresh network or change to rights/physical state at read time.
        assert requests == prefix + ["detail", "inventory"]
        assert reopened.entitlements() == [] and reopened.acquisitions() == [] and reopened.placements() == []
    finally:
        reopened.close()
