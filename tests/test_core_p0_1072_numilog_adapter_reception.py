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

from cifereca.core.canonical_sync_stage import observe_canonical_rights_in_sync
from cifereca.core.notice_c_purchase_runtime import install_purchase_runtime
from cifereca.core.notice_c_purchase_descriptive_read import read_purchase_descriptive_notice
from cifereca.core.notice_c_purchase_library_projection import _status_txn
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.plugin_api import Account, from_legacy_rights_snapshot
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
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


def test_real_numilog_adapters_same_read_automatic_c_and_reopen(tmp_path, monkeypatch):
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
    }) + '</script>'
    requests = []

    def private_http(request):
        assert request.method == "GET"
        assert request.url.host == "www.numilog.com"
        assert request.url.path.lower() == "/pages/compte/meslivres.aspx"
        requests.append("inventory")
        return httpx.Response(200, text=inventory, request=request)

    def public_http(request):
        assert request.method == "GET" and str(request.url) == PRODUCT
        assert "cookie" not in request.headers and "authorization" not in request.headers
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
    private = private_module.NumilogSession(
        credential_id=credential, label="Fixture", credentials={}, options={},
        config={}, diagnostics=None, identity_broker=broker,
    )
    account = Account(identity_id=owner, provider_id="numilog",
                      credential_id=credential, unique_id=account_id)
    private.connected = True
    private._accounts = [account]
    public = NumilogPublicSession(credential_id="__public__", label="Public",
                                  credentials={}, options={})
    public.connect()
    path = tmp_path / "reception.sqlite"
    db = Storage(path)
    register(db, owner, "numilog", credential, account=account_id)
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
    runtime = NoticeWorkRuntime(db, tasks)
    wakes = []
    monkeypatch.setattr(runtime, "request_tick", lambda: wakes.append(True))
    snapshots = []
    try:
        executor = install_purchase_runtime(service, runtime)
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
        # The real runtime owns both the purchase work and follow-up aggregate.
        deadline = monotonic() + 10
        notice = None
        while monotonic() < deadline:
            for handle in runtime._pump.tick().handles:
                assert handle.result(timeout=5) == "current"
            notice = read_purchase_descriptive_notice(db, chosen, identity_id=owner)
            if notice is not None:
                break
            Event().wait(.01)
        assert notice is not None
        assert requests == ["inventory", "detail"]
        facts = {fact.field: fact.value for fact in notice.facts}
        assert facts["author"] == "Auteur de recette" and facts["publisher"] == "Editeur de recette"
        assert facts["description"] == "Résumé de recette"
        assert facts["languages"] == "fr" and "isbn" not in facts
        assert not notice.proves_acquired_edition and not notice.authorizes_acquisition
        assert read_purchase_descriptive_notice(db, omitted, identity_id=owner) is None
        assert read_purchase_descriptive_notice(db, chosen, identity_id="B") is None
        again = observe_canonical_rights_in_sync(service, account)
        assert again["purchase_enrichment"]["admitted"] == 0
        assert requests == ["inventory", "detail", "inventory"]
        assert read_purchase_descriptive_notice(db, chosen, identity_id=owner) == notice
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        assert not service.root.exists()
    finally:
        runtime.close()
        tasks.close()
        private.close()
        public.close()
        db.close()
    reopened = Storage(path)
    try:
        assert read_purchase_descriptive_notice(reopened, chosen, identity_id=owner) == notice
        assert read_purchase_descriptive_notice(reopened, omitted, identity_id=owner) is None
    finally:
        reopened.close()
