"""#1883: raw signed URL/redirect integrity, without live accounts or HTTP.

Python urllib.parse removes CR/LF/TAB before reporting a parsed origin. Such
normalization must never silently rewrite the bytes of a signed ACSM URL.
All identifiers and links below are synthetic.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import checked_redirect, checked_url
from cifereca_numilog_html import (
    LIBRARY, ParseError, DeliverySelectionError,
    delivery_url, parse_library_page,
)

SIGNED = (
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm"
    "?action=enterorder&orderid=FICTIONAL&auth=NOT_A_SECRET%2FTEST"
)
READBOOK = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12"
ALLOWED = {
    ("https", "www.numilog.com", 443),
    ("https", "entrepot2r.numilog.com", 443),
}


@pytest.mark.parametrize("unsafe", ("\\n", "\\r", "\\t", "\\u202e", "\\u200b"))
def test_raw_control_in_signed_acsm_is_not_normalized_into_valid_url(unsafe):
    injected = SIGNED.replace("URLLink.acsm", "URLLink" + unsafe + ".acsm")
    with pytest.raises(ProviderError, match="destination réseau inattendue"):
        checked_url(injected, ALLOWED)
    with pytest.raises(DeliverySelectionError, match="missing"):
        delivery_url(
            f'<script>window.location = "{injected}";</script>',
            LIBRARY, allowed_origins=ALLOWED,
        )


@pytest.mark.parametrize("unsafe", ("\\n", "\\r", "\\t", "\\u202e", "\\u200b"))
def test_raw_control_in_redirect_is_rejected_before_urljoin(unsafe):
    destination = SIGNED.replace("auth=", "auth=" + unsafe)
    with pytest.raises(ProviderError, match="redirection réseau inattendue"):
        checked_redirect(READBOOK, destination, ALLOWED)
    # A protocol-valid relative Location from the same trusted origin remains
    # allowed and does not gain permission to call a different host.
    assert checked_redirect(
        READBOOK, "../readBook.ashx?lid=12", ALLOWED,
    ) == READBOOK


def test_safe_signed_query_is_never_decoded_or_rewritten():
    assert checked_url(SIGNED, ALLOWED) == SIGNED
    assert checked_redirect(READBOOK, SIGNED, ALLOWED) == SIGNED
    assert delivery_url(
        f'<a href="{SIGNED.replace("&", "&amp;")}">Get ACSM</a>',
        LIBRARY, allowed_origins=ALLOWED,
    ) == SIGNED
    assert checked_url(SIGNED.replace("&auth=", "&auth=%250A%2526"), ALLOWED).endswith(
        "&auth=%250A%2526"
    )


@pytest.mark.parametrize("unsafe", ("\\n", "\\r", "\\t", "\\u202e", "\\u200b"))
def test_library_readbook_href_is_rejected_before_urljoin(unsafe):
    # A corrupted library anchor cannot be 'fixed' by urljoin into a
    # seemingly valid readBook endpoint at an explicit download.
    href = "/Pages/Compte/read" + unsafe + "Book.ashx?lid=12"
    html = (
        '<table id="myTable"><div><span class="pTitle">Fictional</span>'
        f'<a class="LnkDownloadBook" href="{href}">Download</a>'
        '</div></table>'
    )
    with pytest.raises(ParseError, match="Lien de bibliothèque inattendu"):
        parse_library_page(html, LIBRARY)


@pytest.mark.parametrize("unsafe", ("\\n", "\\t", "\\u202e"))
def test_library_pagination_cannot_normalize_invalid_href(unsafe):
    html = (
        '<table id="myTable"></table>'
        f'<a rel="next" href="/Pages/Compte/Mes{unsafe}Livres.aspx?page=2">next</a>'
    )
    with pytest.raises(ParseError, match="Lien de pagination inattendu"):
        parse_library_page(html, LIBRARY)


def test_raw_network_validation_does_not_coerce_provider_objects():
    class Poison:
        def __str__(self):
            raise AssertionError("PRIVATE_TOKEN")

    with pytest.raises(ProviderError, match="destination réseau inattendue"):
        checked_url(Poison(), ALLOWED)
    with pytest.raises(ProviderError, match="redirection réseau inattendue"):
        checked_redirect(READBOOK, Poison(), ALLOWED)
