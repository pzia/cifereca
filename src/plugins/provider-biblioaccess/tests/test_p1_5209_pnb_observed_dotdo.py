"""#5209: sanitized five-loan field shape, never real credentials or network."""
from __future__ import annotations

from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biblioaccess_delivery_url import classify_loan_delivery
from biblioaccess_loans_page import inspect_myloans_html
from biblioaccess_acsm_delivery import ACSMDeliveryError, stage_loan_acsm

_NUMILOG = "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm?fixture=safe"
_PNB_BASE = (
    "https://pnb-dilicom.centprod.com/ab//cdef/1234567890123/abcd/"
    "abcdefghijklmnopqrstuvwxy/"
)
_PNB = _PNB_BASE + "0000000000000-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.do"
_ACSM = (
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
    b'fulfillmentType="loan"><permissions><display><duration>3600</duration>'
    b'</display></permissions></fulfillmentToken>'
)


def _row(n, url):
    return (
        '<div class="JSProductRow-' + str(10000+n) + '">'
        '<a href="/1409/Catalog/Book/' + str(20000+n) + '">Book</a>'
        '<p class="titleLoans">Fixture</p>'
        '<span class="jsDateEnd">30/11/2026 12:00:00</span>'
        '<div class="downloadbook JSdownloadbook-' + str(10000+n) + '">'
        '<a href="' + url + '">Télécharger</a></div></div>'
    )


def test_observed_five_rows_two_numilog_three_dotdo():
    html = "".join(_row(n, _NUMILOG if n < 3 else _PNB_BASE +
        "0000000000000-" + c * 32 + ".do")
        for n, c in enumerate("AABCD", start=1))
    found = inspect_myloans_html(html)
    assert found.page_loan_row_count == 5
    assert found.page_loan_row_partial_count == 0
    assert [row.delivery_kind for row in found._loan_rows] == [
        "numilog_acsm", "numilog_acsm", "pnb_do", "pnb_do", "pnb_do",
    ]
    assert all(row.acsm_offer_present for row in found._loan_rows)
    assert _PNB not in repr(found)


def test_dotdo_delivery_is_fetched_without_rewriting_opaque_path(tmp_path):
    called = []
    def handler(request):
        called.append((request.method, request.url.path))
        return httpx.Response(200, headers={"content-type": "application/octet-stream"},
                              content=_ACSM)
    result = stage_loan_acsm(_PNB, tmp_path, transport=httpx.MockTransport(handler))
    assert result.read_bytes() == _ACSM
    assert called == [("GET", _PNB.split(".com", 1)[1])]
    assert classify_loan_delivery(_PNB) == "pnb_do"


@pytest.mark.parametrize("url", [
    _PNB.replace("/ab//cdef/", "/ab///cdef/"),
    _PNB.replace("/ab//cdef/", "/ab//../cdef/"),
    _PNB.replace("/ab//cdef/", "/ab/%2f/cdef/"),
    _PNB.replace("1234567890123", "not-an-isbn"),
    _PNB.replace(".do", ".exe"),
    _PNB.replace("0000000000000-", "x-"),
    _PNB.replace("pnb-dilicom.centprod.com", "evil.example"),
    _PNB.replace("https:", "http:"),
    _PNB + "#fragment",
])
def test_unobserved_shape_not_fetched(url, tmp_path):
    assert classify_loan_delivery(url) is None
    with pytest.raises(ACSMDeliveryError, match="url_invalid"):
        stage_loan_acsm(url, tmp_path, transport=httpx.MockTransport(
            lambda _: pytest.fail("unexpected GET")))
    assert list(tmp_path.iterdir()) == []


def test_dotdo_denial_does_not_stage_any_bytes(tmp_path):
    calls = []
    def denied(request):
        calls.append(request.url.path)
        return httpx.Response(403, content=b"private")
    with pytest.raises(ACSMDeliveryError, match="acsm_denied"):
        stage_loan_acsm(_PNB, tmp_path, transport=httpx.MockTransport(denied))
    assert calls == [_PNB.split(".com", 1)[1]]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("location", "code"), [
    ("https://untrusted.invalid/fulfillment", "redirect_refused"),
    ("http://acs4.cantook.net/fulfillment", "redirect_refused"),
    ("https://acs4.cantook.net:444/fulfillment", "redirect_refused"),
    ("https://acs4.cantook.net/%2foutside", "redirect_refused"),
    ("", "acsm_redirect"),
])
def test_observed_dotdo_redirect_refusal_is_not_staged(tmp_path, location, code):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.host, request.url.path))
        return httpx.Response(302, headers={"location": location} if location else {})
    with pytest.raises(ACSMDeliveryError, match=code):
        stage_loan_acsm(_PNB, tmp_path, transport=httpx.MockTransport(handler))
    assert calls == [("GET", "pnb-dilicom.centprod.com",
                      _PNB.split(".com", 1)[1])]
    assert not list(tmp_path.iterdir())


def test_observed_dotdo_redirect_keeps_path_and_no_credentials(tmp_path):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.host, request.url.path,
                      request.headers.get("cookie"),
                      request.headers.get("authorization")))
        if request.url.host == "pnb-dilicom.centprod.com":
            return httpx.Response(302, headers={
                "location": "https://acs4.cantook.net/fulfillment/opaque"})
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"},
            content=_ACSM)
    path = stage_loan_acsm(
        _PNB, tmp_path, transport=httpx.MockTransport(handler))
    assert path.read_bytes() == _ACSM
    assert calls == [
        ("GET", "pnb-dilicom.centprod.com", _PNB.split(".com", 1)[1],
         None, None),
        ("GET", "acs4.cantook.net", "/fulfillment/opaque", None, None),
    ]
    assert len(list(tmp_path.glob("*.acsm"))) == 1
