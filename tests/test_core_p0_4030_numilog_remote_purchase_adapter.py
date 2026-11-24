"""#4030 actual Numilog private adapter behind explicit local documentary review.

Only the authenticated session and HTTP response bodies are synthetic.
The Core reader, parser, typed observation and local EPUB proof are real.
"""
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.core import local_origin_remote_purchases as remote
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from cifereca.plugin_api import Account
from tests.test_core_p0_4030_public_catalogue import rig, store
from tests.test_core_p0_3171_atomic_publication_v14 import register
from tests.test_core_p0_4030_remote_purchase_windows import _image

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-numilog"
PRODUCT = "https://www.numilog.com/123/ouvrage.ebook"
OTHER = "https://www.numilog.com/456/autre.ebook"
SHARE = "https://www.numilog.com/Pages/Readzis/Recommand.ashx?LivreId=43&amp;SN=FB"


def card(lid, href):
    return (f'<tr><td><a href="{href}"><span class="pTitle">Roman commun</span></a>'
            f'<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m=dwld">Get</a>'
            '</td></tr>')


@pytest.mark.parametrize("change", ["none", "cancel", "rebind"])
def test_explicit_remote_purchase_uses_real_numilog_meslivres_only(
    rig, tmp_path, monkeypatch, change,
):
    service, public, request, calls, path = rig
    monkeypatch.syspath_prepend(str(PLUGIN))
    import cifereca_numilog as private_module
    import cifereca_numilog_public_client as public_client

    credential = "fixture-credential"
    account_id = "numilog:" + hashlib.sha256(credential.encode()).hexdigest()[:20]
    account = Account(identity_id="A", provider_id="numilog",
                      credential_id=credential, unique_id=account_id)
    register(service.storage, "A", "numilog", credential, account=account_id)
    owner = ["A"]
    active = [True]
    broker = NS(accounts=lambda identity: [
        NS(provider_id="numilog", account_id=account_id,
           credential_id=credential, identity_id=owner[0], scope_version=2)
    ] if identity == owner[0] else [])
    inventory = ('<html><body><table id="myTable"><tbody>'
                 + card(42, PRODUCT) + card(43, SHARE) + card(44, OTHER)
                 + '</tbody></table></body></html>')

    def private_http(req):
        assert req.method == "GET" and req.url.host == "www.numilog.com"
        assert req.url.path.lower() == "/pages/compte/meslivres.aspx"
        assert "Roman commun" not in str(req.url)
        calls.append("inventory")
        if change == "cancel":
            active[0] = False
        elif change == "rebind":
            owner[0] = "B"
        return httpx.Response(200, text=inventory, request=req)

    def transport(**kwargs):
        assert kwargs.get("producer") == "provider.numilog"
        return httpx.Client(transport=httpx.MockTransport(private_http), trust_env=False,
                            headers=kwargs.get("headers"), follow_redirects=False)

    monkeypatch.setattr(private_module.HTTPX_TRANSPORT, "client", transport)
    monkeypatch.setattr(public_client, "fetch_public_detail",
                        lambda *_a, **_kw: pytest.fail("no enrichment"))
    monkeypatch.setattr(public_client, "resolve_readzis_recommendation",
                        lambda *_a, **_kw: pytest.fail("no tracking"))
    session = private_module.NumilogSession(
        credential_id=credential, label="Fixture", credentials={}, options={},
        config={}, diagnostics=None, identity_broker=broker,
    )
    try:
        session.connected = True
        session._accounts = [account]
        service.accounts = [account]
        service._sessions["private"] = session
        service.sync_source_enabled = lambda a: a is account
        service._provider_enabled_for_identity = lambda p, i: p == "numilog" and i == "A"
        monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
        known = run_local_origin_suggestion(service, request)
        choice, = remote.available_remote_purchase_catalogues(
            service, request, evidence=known["public_comparison_evidence"])
        before, data = _image(service.storage), path.read_bytes()
        assert not calls
        result = remote.compare_remote_purchase_catalogue(
            service, choice, confirmed=True, still_current=lambda: active[0])
        assert calls == ["inventory"]
        if change == "none":
            assert result["status"] == "partial"
            assert len(result["candidates"]) == 2
            assert len({r.edition_id for r in result["candidates"]}) == 2
            assert all(r.title == "Roman commun" and r.comparison ==
                       "title_concordant_author_unknown" for r in result["candidates"])
        else:
            assert not result["candidates"]
        assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                          "association_authorized", "may_write_origin_or_right",
                                          "local_description_transmitted"))
        assert all(url not in repr(result) for url in (PRODUCT, OTHER, SHARE))
        assert _image(service.storage) == before and path.read_bytes() == data
    finally:
        session.close()
