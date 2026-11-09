"""P1 #5209: BiblioAccess authenticated 5-loan delivery, 2 Numilog + 3 PNB.

Synthetic MyLoans HTML and supplier GETs. Never send or persist actual
borrower/cookie/signed delivery URLs, never request a new loan.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in (
    "biblioaccess_observations", "biblioaccess_delivery_url",
    "biblioaccess_loans_page", "biblioaccess_acsm_delivery",
):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)

links = sys.modules["biblioaccess_delivery_url"]
loans = sys.modules["biblioaccess_loans_page"]
delivery = sys.modules["biblioaccess_acsm_delivery"]

NUMILOG = "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm?code=SYNTHETIC"
PNB = "https://pnb-dilicom.centprod.com/loans/selection/do?ticket=SYNTHETIC"
ACSM = (
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
    b'fulfillmentType="loan"><permissions><display><duration>3600</duration>'
    b'</display></permissions></fulfillmentToken>'
)


def _row(number: int, url: str):
    product = str(10000 + number)
    book = str(20000 + number)
    return (
        '<div class="JSProductRow-' + product + '">'
        '<a href="/1409/Catalog/Book/' + book + '">Notice</a>'
        '<p class="titleLoans">Tome ' + str(number) + '</p>'
        '<span class="jsDateEnd">09/11/2026 12:30:00</span>'
        '<div class="downloadbook JSdownloadbook-' + product + '">'
        '<a href="' + url + '">Télécharger</a>'
        '</div></div>'
    )


def test_five_qualified_rows_two_numilog_three_pnb_are_positive_and_private():
    html = (
        '<form id="logoutForm" method="post" '
        'action="/1409/Account/LogOff"></form>'
        + _row(1, NUMILOG) + _row(2, NUMILOG)
        + _row(3, PNB) + _row(4, PNB) + _row(5, PNB)
    )
    report = loans.inspect_myloans_html(html)
    assert report.logout_form_observed
    assert report.page_loan_row_count == 5
    assert report.page_loan_row_partial_count == 0
    assert [row.acsm_offer_present for row in report._loan_rows] == [True] * 5
    assert [row.delivery_kind for row in report._loan_rows] == [
        "numilog_acsm", "numilog_acsm", "pnb_do", "pnb_do", "pnb_do",
    ]
    assert not report.inventory_verified and not report.identity_verified
    assert "SYNTHETIC" not in repr(report)
    assert "SYNTHETIC" not in repr(report._loan_rows)


def test_three_pnb_gets_can_return_direct_or_redirected_adobe_acsm(tmp_path):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.host, request.url.path,
                      request.headers.get("cookie")))
        if request.url.path == "/loans/selection/do":
            if request.url.params.get("ticket") == "direct":
                return httpx.Response(200, content=ACSM)
            if request.url.params.get("ticket") == "eden":
                return httpx.Response(
                    302, headers={"location":
                        "https://www.edenlivres.fr/fulfillment/SYNTHETIC"},
                )
            return httpx.Response(
                302, headers={"location":
                    "https://acs4.cantook.net/fulfillment/SYNTHETIC"},
            )
        assert request.url.host in {"www.edenlivres.fr", "acs4.cantook.net"}
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"},
            content=ACSM,
        )
    for ticket in ("direct", "eden", "cantook"):
        url = PNB.replace("ticket=SYNTHETIC", "ticket=" + ticket)
        path = delivery.stage_loan_acsm(
            url, tmp_path, transport=httpx.MockTransport(handler),
        )
        assert path.exists() and path.read_bytes() == ACSM
        assert path.suffix == ".acsm"
    assert len(list(tmp_path.glob("*.acsm"))) == 3
    assert len(calls) == 5
    assert all(method == "GET" and cookie is None
               for method, host, path, cookie in calls)


@pytest.mark.parametrize(("reply", "code"), [
    (httpx.Response(403, content=b"PRIVATE"), "acsm_denied"),
    (httpx.Response(404), "acsm_missing"),
    (httpx.Response(200, content=b"<html>login</html>"), "structure_invalid"),
    (httpx.Response(302), "acsm_redirect"),
    (httpx.Response(302, headers={"location":
        "http://pnb-dilicom.centprod.com/next"}), "redirect_refused"),
    (httpx.Response(302, headers={"location":
        "https://malicious.invalid/download.acsm"}), "redirect_refused"),
])
def test_pnb_wrong_response_never_stages_unverified_content(tmp_path, reply, code):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return reply
    with pytest.raises(delivery.ACSMDeliveryError, match=code):
        delivery.stage_loan_acsm(
            PNB, tmp_path, transport=httpx.MockTransport(handler),
        )
    assert calls == ["pnb-dilicom.centprod.com"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("candidate", [
    "http://pnb-dilicom.centprod.com/loan/id/do",
    "https://pnb-dilicom.centprod.com.evil.invalid/loan/id/do",
    "https://reader:secret@pnb-dilicom.centprod.com/loan/id/do",
    "https://pnb-dilicom.centprod.com/loan/%2e%2e/do",
    "https://pnb-dilicom.centprod.com/loan/%2fexternal/do",
    "https://pnb-dilicom.centprod.com/loan//do",
    "https://pnb-dilicom.centprod.com/loan/id/do#fragment",
    "https://pnb-dilicom.centprod.com/loan/id/no",
    "https://pnb-dilicom.centprod.com:444/loan/id/do",
])
def test_untrusted_pnb_link_never_reaches_http(tmp_path, candidate):
    assert links.classify_loan_delivery(candidate) is None
    with pytest.raises(delivery.ACSMDeliveryError, match="url_invalid"):
        delivery.stage_loan_acsm(
            candidate, tmp_path,
            transport=httpx.MockTransport(
                lambda request: pytest.fail("unexpected PNB GET")),
        )
    assert not list(tmp_path.iterdir())


def test_redirect_cycle_and_excessive_hops_refused_before_any_file(tmp_path):
    def cycle(request):
        return httpx.Response(302, headers={"location": PNB})
    with pytest.raises(delivery.ACSMDeliveryError, match="redirect_cycle"):
        delivery.stage_loan_acsm(
            PNB, tmp_path, transport=httpx.MockTransport(cycle),
        )
    assert not list(tmp_path.iterdir())
