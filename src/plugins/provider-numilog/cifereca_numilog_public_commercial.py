"""Observed Numilog Product/Book commercial-offer and cover evidence (#1886).

These values describe a public product listing, NEVER which EPUB/PDF an
account acquired. No network, cookies, URLs to follow or DRM rights.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from cifereca_numilog_html import public_book_url

_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,7})\.[0-9]{2}\Z")
_COVER = re.compile(r"https://couverture\.numilog\.com/[0-9]{13}_TRESGRANDE\.jpg\Z")
_AVAILABILITY = {
    "https://schema.org/InStock": "in_stock",
    "https://schema.org/OutOfStock": "out_of_stock",
    "https://schema.org/PreOrder": "pre_order",
}
_SOURCES = {
    "offer_price": "Product.offers.price",
    "offer_currency": "Product.offers.priceCurrency",
    "offer_availability": "Product.offers.availability",
    "cover_url": "Product.image",
}
_SCOPE = "commercial_product_not_purchased_format"


class PublicCommercialError(ValueError):
    def __init__(self):
        super().__init__("Numilog public commercial: evidence_unverified")


def _proof(key, url):
    return {
        "source": _SOURCES[key],
        "source_url": url,
        "kind": "observed_public_jsonld",
        "scope": _SCOPE,
        "edition_proven": False,
        "rights_proven": False,
    }


def _url_ok(url):
    return (
        type(url) is str and public_book_url(url) == url
        and urlsplit(url).scheme == "https"
        and urlsplit(url).netloc == "www.numilog.com"
    )


def extract_public_commercial(node, product_url):
    """Return an opt-in, fixed-shape proof of explicit JSON-LD Product fields.

    A matching Product/Book node has already been selected by the caller.
    Absent/multiple/nonconforming offers produce no speculative price; they
    do not prevent independent cover proof. Signed offer URLs are NEVER read.
    The observed Product/Book JSON-LD does not prove any purchased format.
    """
    if not _url_ok(product_url) or type(node) is not dict:
        raise PublicCommercialError()
    fields = {}
    if (node.get("@type") == "Product"
            and node.get("additionalType") in (
                "Book", "https://schema.org/Book", "http://schema.org/Book"
            )
            and type(node.get("sku")) is str
            and node["sku"] == urlsplit(product_url).path.split("/")[1]
            and node.get("url") == product_url):
        raw = node.get("offers")
        if type(raw) is dict and raw.get("@type") == "Offer":
            price = raw.get("price")
            currency = raw.get("priceCurrency")
            if (type(price) is str and _PRICE.fullmatch(price)
                    and type(currency) is str and currency == "EUR"):
                fields["offer_price"] = price
                fields["offer_currency"] = currency
            available = raw.get("availability")
            if type(available) is str and available in _AVAILABILITY:
                fields["offer_availability"] = _AVAILABILITY[available]
        cover = node.get("image")
        if type(cover) is str and _COVER.fullmatch(cover):
            fields["cover_url"] = cover
    if not fields:
        return None
    return {
        "product_url": product_url,
        "fields": fields,
        "field_evidence": {key: _proof(key, product_url) for key in fields},
    }


def validated_public_commercial(detail, product_url):
    """Fail closed on caller-supplied evidence before public/owned projection."""
    if type(detail) is not dict or not _url_ok(product_url):
        raise PublicCommercialError()
    data = detail.get("public_commercial")
    if data is None:
        return None
    if (type(data) is not dict
            or set(data) != {"product_url", "fields", "field_evidence"}
            or data["product_url"] != product_url
            or type(data["fields"]) is not dict
            or type(data["field_evidence"]) is not dict
            or not data["fields"]
            or set(data["fields"]) != set(data["field_evidence"])
            or not set(data["fields"]).issubset(_SOURCES)):
        raise PublicCommercialError()
    fields = data["fields"]
    if ("offer_price" in fields) != ("offer_currency" in fields):
        raise PublicCommercialError()
    for key, value in fields.items():
        if type(value) is not str or data["field_evidence"][key] != _proof(key, product_url):
            raise PublicCommercialError()
        if key == "offer_price" and not _PRICE.fullmatch(value):
            raise PublicCommercialError()
        if key == "offer_currency" and value != "EUR":
            raise PublicCommercialError()
        if key == "offer_availability" and value not in _AVAILABILITY.values():
            raise PublicCommercialError()
        if key == "cover_url" and not _COVER.fullmatch(value):
            raise PublicCommercialError()
    return {
        "product_url": product_url,
        "fields": dict(fields),
        "field_evidence": {key: dict(proof) for key, proof in data["field_evidence"].items()},
    }
