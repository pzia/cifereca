"""P0 #2024: safe shape of a Numilog confirmation ref, no real order data."""
import pytest

from cifereca_numilog_delivery_trace import (
    DeliveryTrace, confirmation_query_shape, destination_category,
)
from tests.test_numilog_delivery_observability_p1 import (
    Capture, assert_private_data_absent,
)

BASE = "https://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx"
REF = "FAKE-PRIVATE-ORDER-REFERENCE"


@pytest.mark.parametrize("query,expected", (
    ("ref=" + REF + "&disp=simple", (True, True)),
    ("ref=" + REF + "&disp=1", (True, False)),
    ("ref=&disp=simple", (False, True)),
    ("ref=" + REF + "&ref=OTHER_PRIVATE&disp=simple", (False, True)),
    ("ref=" + REF + "&disp=simple&disp=1", (True, False)),
    ("ref=" + REF + "&disp=simple&invalid", (False, False)),
))
def test_exact_confirmation_query_exposes_only_boolean_evidence(query, expected):
    destination = BASE + "?" + query
    assert destination_category(destination) == ("numilog_site", "confirmation_page")
    assert confirmation_query_shape(destination) == {
        "confirmation_ref_present": expected[0],
        "confirmation_disp_simple": expected[1],
    }
    capture = Capture()
    with DeliveryTrace(capture) as trace:
        trace.event("delivery_http", "redirect", redirect_target=destination, status=302)
        trace.event("delivery_http", "http_ok", destination=destination, status=200)
    rows = [kw["detail"] for _, kw in capture.acquisition()]
    redirect, arrived = rows[1:3]
    assert redirect["redirect_target_kind"] == "confirmation_page"
    assert redirect["redirect_target_origin_kind"] == "numilog_site"
    assert redirect["redirect_confirmation_ref_present"] is expected[0]
    assert redirect["redirect_confirmation_disp_simple"] is expected[1]
    assert arrived["path_kind"] == "confirmation_page"
    assert arrived["confirmation_ref_present"] is expected[0]
    assert arrived["confirmation_disp_simple"] is expected[1]
    assert REF not in repr(capture.events)
    assert "OTHER_PRIVATE" not in repr(capture.events)
    assert_private_data_absent(capture)


@pytest.mark.parametrize("url", (
    "https://attacker.invalid/Pages/Commande/ConfirmationPaiement.aspx?ref=" + REF,
    "http://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?ref=" + REF,
    "https://www.numilog.com/Other/ConfirmationPaiement.aspx?ref=" + REF,
    "https://www.numilog.com/Login.Aspx?ref=" + REF,
    "https://user:password@www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?ref=" + REF,
    "https://www.numilog.com:8443/Pages/Commande/ConfirmationPaiement.aspx?ref=" + REF,
    "https://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx?ref=" + REF + "#hidden",
))
def test_unqualified_routes_never_advertise_confirmation_query_shape(url):
    assert destination_category(url)[1] != "confirmation_page"
    assert confirmation_query_shape(url) == {}
    capture = Capture()
    with DeliveryTrace(capture) as trace:
        trace.event("delivery_http", "http_ok", destination=url)
    row = [kw["detail"] for _, kw in capture.acquisition()][1]
    assert "confirmation_ref_present" not in row
    assert REF not in repr(capture.events)
    assert_private_data_absent(capture)


def test_rejected_redirect_does_not_claim_a_confirmation_destination():
    capture = Capture()
    with DeliveryTrace(capture) as trace:
        trace.event(
            "delivery_http", "redirect", status=302,
            destination="https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12",
        )
    row = [kw["detail"] for _, kw in capture.acquisition()][1]
    assert "redirect_target_kind" not in row
    assert "redirect_confirmation_ref_present" not in row
    assert_private_data_absent(capture)
