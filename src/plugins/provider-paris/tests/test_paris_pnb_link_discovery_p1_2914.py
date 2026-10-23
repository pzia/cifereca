"""#2914 — non-identifying evidence when official PNB borrow link is absent."""
from __future__ import annotations

import logging
from urllib.parse import quote

import pytest

from cifereca_paris_client import ParisClient, ParisError


def _payload(html, *, can=True):
    return {"d": {"ItemHoldingsData": {"CanLoan": can}, "HtmlView": html}}


def _borrow_href(inner="/fake/loan-book.ashx?ean=9782000000000"):
    return "/numerique/ASSARedirect.ashx?url=" + quote(inner, safe="")


def _trace(caplog):
    return "\n".join(record.getMessage() for record in caplog.records
                     if "link-discovery" in record.getMessage())


def test_canonical_signed_link_is_still_the_only_actionable_candidate(caplog):
    href = _borrow_href()
    page = f'<div><a href="{href}">Emprunter</a></div>'
    with caplog.at_level(logging.INFO, logger="cifereca"):
        selected = ParisClient._pnb_loan_href(_payload(page))
    assert selected == href
    text = _trace(caplog)
    assert "anchors=1 candidates=1" in text
    assert "assaredirect_hrefs=1" in text and "loan_book_hrefs=1" in text
    assert "candidate_site=bibliotheques.paris.fr" in text
    assert "/numerique/" not in text
    assert "9782000000000" not in text


def test_four_irrelevant_anchors_are_diagnosed_without_new_mutation(caplog):
    html = (
        '<a href="/other">Read</a>'
        '<a href="https://PRIVATE_ACCOUNT.example.test/loan?token=PRIVATE_TOKEN">Other</a>'
        '<a href="javascript:void(0)">Borrow</a>'
        '<a href="/fake/loan-book.ashx">More</a>'
        '<form method="post" action="/borrow?token=PRIVATE_TOKEN">'
        '<button>Emprunter</button></form>'
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with pytest.raises(ParisError, match="Aucun emprunt lancé"):
            ParisClient._pnb_loan_href(_payload(html))
    text = _trace(caplog)
    assert "anchors=4 candidates=0" in text
    assert "forms=1 post_forms=1 form_actions=1" in text
    assert "assaredirect_hrefs=0 loan_book_hrefs=1" in text
    assert "dom_assaredirect=False dom_loan_book=True" in text
    assert "rejected_scheme=1" in text
    assert "external_hrefs=1" in text
    assert "candidate_site=unknown" in text
    assert "/borrow" not in text
    for secret in ("PRIVATE_ACCOUNT", "PRIVATE_TOKEN", "javascript:", "loan?token"):
        assert secret not in text


def test_form_with_only_action_remains_non_actionable(caplog):
    html = (
        '<form method="post" action="/numerique/ASSARedirect.ashx">'
        '<input name="loan-book.ashx" type="hidden" value="PRIVATE_TOKEN">'
        '</form>'
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with pytest.raises(ParisError, match="Aucun emprunt lancé"):
            ParisClient._pnb_loan_href(_payload(html))
    text = _trace(caplog)
    assert "forms=1 post_forms=1 form_actions=1" in text
    assert "anchors=0 candidates=0" in text
    assert "dom_assaredirect=True dom_loan_book=True" in text
    assert "PRIVATE_TOKEN" not in text


def test_ambiguous_signed_links_remain_blocked_without_picking_first(caplog):
    html = ''.join(
        f'<a href="{_borrow_href("/fake/loan-book.ashx?ean="+num)}">Book</a>'
        for num in ("9782000000000", "9782000000001")
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with pytest.raises(ParisError, match="Aucun emprunt lancé"):
            ParisClient._pnb_loan_href(_payload(html))
    text = _trace(caplog)
    assert "anchors=2 candidates=2" in text
    assert "candidate_site=unknown" in text
    for num in ("9782000000000", "9782000000001"):
        assert num not in text


def test_canloan_false_stops_before_html_classification(caplog):
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with pytest.raises(ParisError, match="Emprunt PNB impossible"):
            ParisClient._pnb_loan_href(_payload('<a href="/unsafe">x</a>', can=False))
    assert "link-discovery" not in _trace(caplog)


def test_no_external_url_is_ever_confirmed_just_from_pattern(caplog):
    # The extraction is deliberately not a network operation and the
    # downstream resolver still owns the host/redirect policy. Evidence
    # reports a closed external class; it must not allow arbitrary origins.
    html = (
        '<a href="https://host.PRIVATE.example/ASSARedirect.ashx?url='
        '%2Ffake%2Floan-book.ashx%3Ftoken%3DPRIVATE">Borrow</a>'
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        candidate = ParisClient._pnb_loan_href(_payload(html))
    assert candidate.startswith("https://host.PRIVATE.example/")
    text = _trace(caplog)
    assert "candidate_site=external" in text
    assert "PRIVATE" not in text
