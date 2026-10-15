"""#1844: synthetic Numilog ACSM origin and signed-query boundary.

No real order ID, auth value, account or live HTTP request.
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import NumilogSession, checked_url
from cifereca_numilog_html import delivery_url, ParseError


SIGNED = (
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm"
    "?action=enterorder&ordersource=Numilog&orderid=FAKE-ORDER"
    "&resid=urn%3Auuid%3AFICTIONAL&rights=%24fake%23ever"
    "&dateval=1111111111&auth=SYNTHETIC_SIGNATURE"
)


class Diagnostics:
    def add(self, *args, **kwargs):
        pass


def session():
    return NumilogSession(
        credential_id="fixture", label="Synthetic",
        credentials={"email": "fixture@example.test", "password": "synthetic"},
        options={}, config={}, diagnostics=Diagnostics(),
    )


def test_exact_https_origin_and_signed_query_are_preserved():
    s = session()
    try:
        assert checked_url(SIGNED, s.allowed_origins) == SIGNED
        assert checked_url(SIGNED) == SIGNED
        assert ("https", "entrepot2r.numilog.com", 443) in s.allowed_origins
        assert urlsplit(checked_url(SIGNED)).query == urlsplit(SIGNED).query
    finally:
        s.close()


@pytest.mark.parametrize("url", (
    SIGNED.replace("https://", "http://", 1),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com.evil.example"),
    SIGNED.replace("entrepot2r.numilog.com", "entrepot2r.numilog.com:8443"),
    SIGNED.replace("entrepot2r.numilog.com", "other.numilog.com"),
    SIGNED.replace("https://", "https://user:pass@", 1),
    SIGNED + "#fragment",
))
def test_untrusted_variants_remain_forbidden(url):
    s = session()
    try:
        with pytest.raises(ProviderError):
            checked_url(url, s.allowed_origins)
    finally:
        s.close()


@pytest.mark.parametrize("html", (
    f'<a href="{SIGNED}">Télécharger</a>',
    f'<script>window.location = "{SIGNED}";</script>',
))
def test_html_delivery_keeps_exact_synthetic_signature(html):
    s = session()
    try:
        assert delivery_url(html, s.library_url, allowed_origins=s.allowed_origins) == SIGNED
    finally:
        s.close()


def test_ambiguous_delivery_is_rejected():
    s = session()
    try:
        with pytest.raises(ParseError):
            delivery_url(
                f'<a href="{SIGNED}">A</a><a href="{SIGNED}&duplicate=1">B</a>',
                s.library_url, allowed_origins=s.allowed_origins,
            )
    finally:
        s.close()
