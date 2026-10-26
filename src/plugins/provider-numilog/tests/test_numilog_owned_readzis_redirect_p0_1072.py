"""P0 #1072: Readzis is only resolved for one explicitly selected owned book.

All cards, identities and HTTP results are synthetic. No account, ACSM,
fulfillment, live Numilog request, purchase or real tracking ID is used.
"""
from __future__ import annotations

import json
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_html import parse_library_page, readzis_recommendation_url
from cifereca_numilog_public_detail import parse_public_detail_jsonld
import cifereca_numilog_public_client as public_client


SHARE = "https://www.numilog.com/Pages/Readzis/Recommand.ashx?LivreId=42&SN=FB"
BOOK = "https://www.numilog.com/42/fiche-test.ebook"
TRACK = BOOK + "?trackid=E6709AD2-34FA-4A9D-B22D-C0A7CE3ABC53&utm_source=readzis"
SECRET = "PRIVATE_SYNTHETIC_TOKEN"


def html_card(lid="42", href=SHARE, title="Fiche test"):
    share = ('<a href="' + href.replace("&", "&amp;") + '">Partager</a>') if href else ''
    return ('<table id="myTable"><tbody><tr><td>'
            '<a class="pTitle">' + title + '</a>'
            '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid='
            + lid + '">Lire</a>' + share + '</td></tr></tbody></table>')


def parsed(html, *, selected=True):
    return parse_library_page(
        html, strict_public_enrichment_selector=(
            (lambda row: row["lid"] == "42") if selected else None
        ),
    )[0]


def test_share_is_transient_and_scoped_to_exact_fresh_owned_card():
    assert parsed(html_card(), selected=False)[0].get("readzis_url") is None
    assert parsed(html_card())[0]["readzis_url"] == SHARE
    assert parsed(html_card(lid="43"))[0].get("readzis_url") is None
    assert readzis_recommendation_url(SHARE, "42") == SHARE
    assert readzis_recommendation_url(SHARE, "43") is None


@pytest.mark.parametrize("bad", [
    SHARE.replace("LivreId=42", "LivreId=43"),
    SHARE + "&extra=1",
    SHARE + "&SN=FB",
    SHARE.replace("SN=FB", "SN=XX"),
    SHARE.replace("www.numilog.com", "evil.invalid"),
    SHARE.replace("https:", "http:"),
    SHARE.replace("www.numilog.com", "www.numilog.com:443"),
    SHARE.replace("?LivreId", "#frag?LivreId"),
    SHARE.replace("42", "42%00"),
    SHARE.replace("Recommand.ashx", "../Readzis/Recommand.ashx"),
])
def test_hostile_share_never_proves_owned_product(bad):
    assert readzis_recommendation_url(bad, "42") is None


def test_explicit_card_refuses_mismatched_or_duplicate_share():
    with pytest.raises(ValueError, match="Readzis non qualifié"):
        parsed(html_card(href=SHARE.replace("LivreId=42", "LivreId=43")))
    with pytest.raises(ValueError, match="Readzis non qualifié"):
        parsed(html_card(href=SHARE + "&extra=1"))


class AnonymousRedirect:
    location = TRACK
    status = 302
    calls = []

    def __init__(self, **kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert "Cookie" not in kwargs["headers"]
        self.__class__.calls.append(("client", kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url):
        assert (method, url) == ("GET", SHARE)
        self.__class__.calls.append(("get", url))
        return self

    @property
    def status_code(self):
        return self.__class__.status

    @property
    def headers(self):
        return {"location": self.__class__.location, "content-type": "text/html"}

    def iter_bytes(self):
        raise AssertionError("no body read or redirect follow")


def test_redirect_disables_follow_and_strips_tracking(monkeypatch):
    AnonymousRedirect.status, AnonymousRedirect.location, AnonymousRedirect.calls = 302, TRACK, []
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", AnonymousRedirect)
    assert public_client.resolve_readzis_recommendation(SHARE, lid="42") == BOOK
    assert [c for c in AnonymousRedirect.calls if c[0] == "get"] == [("get", SHARE)]
    assert TRACK not in repr(AnonymousRedirect.calls)


@pytest.mark.parametrize("location,status", [
    (TRACK.replace("/42/", "/43/"), 302),
    (TRACK.replace("www.numilog.com", "evil.invalid"), 302),
    (TRACK.replace("https", "http"), 302),
    ("https://www.numilog.com/Pages/Compte/MesLivres.aspx", 302),
    (BOOK + "#private", 302),
    (BOOK.replace("www.numilog.com", "www.numilog.com:443"), 302),
    (BOOK, 200), (BOOK, 307), (BOOK, 403), (BOOK, 429),
])
def test_unqualified_redirects_never_start_product_get(monkeypatch, location, status):
    class Case(AnonymousRedirect):
        pass
    Case.status, Case.location, Case.calls = status, location, []
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", Case)
    with pytest.raises(ProviderError) as caught:
        public_client.resolve_readzis_recommendation(SHARE, lid="42")
    assert SECRET not in str(caught.value)
    assert [c for c in Case.calls if c[0] == "get"] == [("get", SHARE)]


@pytest.mark.parametrize("query", [
    "?trackid=" + SECRET,
    "?trackid=" + SECRET + "%2F",
    "?utm_source=readzis&trackid=anything&unknown=value",
    "?anything=signed-looking-data",
    "?arbitrary=" + ("x" * 1024),
    "?trackid=" + SECRET + " with spaces",
    "",
])
def test_redirect_query_is_ignored_entirely(monkeypatch, query):
    class Case(AnonymousRedirect):
        pass
    Case.status, Case.location, Case.calls = 302, BOOK + query, []
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client", Case)
    assert public_client.resolve_readzis_recommendation(SHARE, lid="42") == BOOK
    assert [c for c in Case.calls if c[0] == "get"] == [("get", SHARE)]
    assert query not in repr(Case.calls) if query else True


def test_unqualified_source_never_opens_socket(monkeypatch):
    monkeypatch.setattr(public_client.HTTPX_TRANSPORT, "client",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("HTTP")))
    with pytest.raises(ProviderCapabilityError):
        public_client.resolve_readzis_recommendation(SHARE, lid="43")


def session(account="synthetic-a"):
    s = NumilogSession(credential_id=account, label="Numilog",
                       credentials={}, options={}, config={}, diagnostics=None)
    s.connected = True
    row = parsed(html_card())[0]
    s._rows = lambda **kw: [row]
    return s, row


def detail():
    raw = '<script type="application/ld+json">' + json.dumps({
        "@type": "Book", "name": "Fiche test", "url": BOOK,
        "description": "Description publique attestée.",
    }) + "</script>"
    return parse_public_detail_jsonld(raw, BOOK)


def test_explicit_enrichment_keeps_entitlement_and_never_replays_trackid(monkeypatch):
    s, row = session()
    calls = []
    monkeypatch.setattr(public_client, "resolve_readzis_recommendation",
                        lambda url, *, lid: calls.append(("share", url, lid)) or BOOK)
    monkeypatch.setattr(public_client, "fetch_public_detail",
                        lambda url: calls.append(("detail", url)) or detail())
    try:
        assert s.library()[0].friendly_url is None
        assert calls == []  # No share visits on plain inventory refresh.
        result = s.enrich_owned_direct_public_detail(s._normalize(row))
        assert calls == [("share", SHARE, "42"), ("detail", BOOK)]
        assert result.friendly_url == BOOK
        assert result.metadata["public_enrichment"]["status"] == "attested_owned_readzis_redirect"
        assert result.metadata["public_detail_enrichment"]["association_evidence"] == "numilog.library_readzis_redirect"
        assert result.metadata["public_detail_enrichment"]["format_edition_proven"] is False
        assert result.owned is True and result.downloadable is True
        assert result.download_url is None and result.format is None
        assert SECRET not in repr(result)
        assert "trackid" not in repr(result)
        assert "readzis_url" not in repr(result)
    finally:
        s.close()


def test_existing_direct_title_link_skips_readzis(monkeypatch):
    s, row = session()
    row["public_url"] = BOOK
    monkeypatch.setattr(public_client, "resolve_readzis_recommendation",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("readzis")))
    monkeypatch.setattr(public_client, "fetch_public_detail", lambda url: detail())
    try:
        result = s.enrich_owned_direct_public_detail(s._normalize(row))
        assert result.metadata["public_enrichment"]["status"] == "attested_owned_title_link"
    finally:
        s.close()


def test_wrong_account_and_absent_purchase_never_resolve(monkeypatch):
    alice, row = session()
    bob, _ = session("synthetic-b")
    calls = []
    bob._rows = lambda **kw: calls.append("private") or []
    monkeypatch.setattr(public_client, "resolve_readzis_recommendation",
                        lambda *a, **kw: calls.append("share") or BOOK)
    try:
        with pytest.raises(ProviderError):
            bob.enrich_owned_direct_public_detail(alice._normalize(row))
        assert calls == []
        with pytest.raises(ProviderError):
            bob.enrich_owned_direct_public_detail(bob._normalize(row))
        assert calls == ["private"]
    finally:
        alice.close()
        bob.close()
