"""#1886: observed Numilog Product/Book public offer and image, never ownership.

Real reduced anonymous Product/Book HTML plus strictly synthetic mutations.
No HTTP call or account cookie; product offers are not format-level grants.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public_detail import parse_public_detail_jsonld, merge_public_detail
from cifereca_numilog_public_commercial import (
    extract_public_commercial, validated_public_commercial, PublicCommercialError,
)
from cifereca_numilog_enrichment import merge_attested_owned_public_detail
from cifereca_numilog_public import parse_public_search

ROOT = Path(__file__).parent / "fixtures"
URL = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
SOURCE = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=loup"
HTML = (ROOT / "observed_public_blackbone.html").read_text()


def observed():
    return parse_public_detail_jsonld(HTML, URL)


def mutated(**updates):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', HTML, re.S)
    node = json.loads(match.group(1))
    node.update(updates)
    return parse_public_detail_jsonld(
        HTML[:match.start(1)] + json.dumps(node) + HTML[match.end(1):], URL
    )


def commercial_row():
    card = parse_public_search(
        (ROOT / "observed_search-loup-0.html").read_text(), SOURCE
    ).items[0]
    return replace(
        card,
        title=observed()["fields"]["title"], friendly_url=URL,
        record_id="numilog:public:1171334", source_provider="numilog-catalog",
        metadata={"source_url": SOURCE, "source_item_id": "1171334"},
    )


def owned_row():
    public = commercial_row()
    return replace(
        public, record_id="numilog:private:42", source_provider="numilog",
        source_credential="alice", source_collection="library",
        owned=True, downloadable=True, download_url=None,
        price=None, currency=None, format=None,
        metadata={
            "public_url_evidence": "numilog.library_title_link",
            "public_enrichment": {
                "status": "attested_listing_match",
                "scope": "public_listing_only",
                "product_url": URL,
                "source_url": SOURCE,
                "field_evidence": {},
            },
        },
    )


def test_observed_product_offer_is_qualified_without_promoting_paper_ean_or_rights():
    detail = observed()
    commercial = detail["public_commercial"]
    assert commercial["product_url"] == URL
    assert commercial["fields"] == {
        "offer_price": "10.99", "offer_currency": "EUR",
        "offer_availability": "in_stock",
        "cover_url": "https://couverture.numilog.com/9782092591086_TRESGRANDE.jpg",
    }
    assert validated_public_commercial(detail, URL) == commercial
    for key, proof in commercial["field_evidence"].items():
        assert proof["kind"] == "observed_public_jsonld"
        assert proof["source_url"] == URL
        assert proof["scope"] == "commercial_product_not_purchased_format"
        assert proof["source"].startswith("Product.")
        assert proof["edition_proven"] is False
        assert proof["rights_proven"] is False
    assert detail["public_identifiers"] == {
        "paper": "9782092591086", "epub": "9782092591093"
    }
    assert detail["owned"] is False and detail["downloadable"] is False
    assert detail["download_url"] is None


def test_public_and_owned_merges_store_offer_only_in_commercial_provenance():
    detail = observed()
    catalog = commercial_row()
    enriched = merge_public_detail(catalog, detail)
    assert enriched.metadata["public_detail"]["public_commercial"] == detail["public_commercial"]
    assert enriched.price == catalog.price and enriched.currency == catalog.currency
    assert enriched.thumbnail_url == catalog.thumbnail_url
    assert enriched.owned is False and enriched.download_url is None
    purchased = owned_row()
    owned_enriched = merge_attested_owned_public_detail(purchased, detail)
    proof = owned_enriched.metadata["public_detail_enrichment"]
    assert proof["public_commercial"] == detail["public_commercial"]
    assert owned_enriched.owned is True and owned_enriched.downloadable is True
    assert owned_enriched.download_url is None
    assert owned_enriched.price is None and owned_enriched.currency is None
    assert owned_enriched.format is None
    assert "isbn_digital" not in owned_enriched.metadata
    assert "owned_format_proven" not in proof["public_commercial"]["fields"]


@pytest.mark.parametrize("offer", [
    [
        {"@type": "Offer", "price": "10.99", "priceCurrency": "EUR"},
        {"@type": "Offer", "price": "0.00", "priceCurrency": "EUR"},
    ],
    {"@type": "Offer", "price": "10.9", "priceCurrency": "EUR"},
    {"@type": "Offer", "price": -1, "priceCurrency": "EUR"},
    {"@type": "Offer", "price": "NaN", "priceCurrency": "EUR"},
    {"@type": "Offer", "price": "10.99", "priceCurrency": "USD"},
    {"@type": "PriceSpecification", "price": "10.99", "priceCurrency": "EUR"},
    {"@type": "Offer", "price": "10.99", "priceCurrency": "PRIVATE"},
])
def test_ambiguous_or_nonconforming_offers_do_not_publish_price(offer):
    commercial = mutated(offers=offer)["public_commercial"]
    assert commercial is not None  # Independent observed cover evidence remains.
    assert "offer_price" not in commercial["fields"]
    assert "offer_currency" not in commercial["fields"]


def test_signed_links_are_not_in_offer_evidence_even_if_present_in_jsonld():
    secret = "https://evil.example/fulfillment?token=PRIVATE_MARKER"
    d = mutated(offers={
        "@type": "Offer", "price": "10.99", "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock", "url": secret,
        "seller": {"password": "PRIVATE_MARKER"},
    })
    assert d["public_commercial"]["fields"]["offer_price"] == "10.99"
    assert secret not in repr(d["public_commercial"])
    assert "PRIVATE_MARKER" not in repr(d["public_commercial"])


def test_missing_or_hostile_public_cover_does_not_become_private_download_url():
    for image in [
        "https://evil.invalid/9782092591086_TRESGRANDE.jpg",
        "https://couverture.numilog.com/9782092591086_TRESGRANDE.jpg?token=PRIVATE",
        "https://couverture.numilog.com/9782092591086_TRESGRANDE.jpg#frag",
        "https://couverture.numilog.com:443/9782092591086_TRESGRANDE.jpg",
        ["https://couverture.numilog.com/9782092591086_TRESGRANDE.jpg"],
    ]:
        d = mutated(image=image)
        assert "cover_url" not in d["public_commercial"]["fields"]
        assert "PRIVATE" not in repr(d["public_commercial"])


@pytest.mark.parametrize("mutation", [
    "price", "currency", "availability", "cover", "url",
    "scope", "rights", "edition", "unknown_field", "missing_evidence",
])
def test_caller_forgery_rejected_before_public_or_owned_projection(mutation):
    d = observed()
    commercial = deepcopy(d["public_commercial"])
    fields = commercial["fields"]
    proofs = commercial["field_evidence"]
    if mutation == "price":fields["offer_price"] = "99999999.99"
    if mutation == "currency":fields["offer_currency"] = "USD"
    if mutation == "availability":fields["offer_availability"] = "downloadable"
    if mutation == "cover":fields["cover_url"] = "https://evil.invalid/PRIVATE"
    if mutation == "url":commercial["product_url"] += "?token=PRIVATE"
    if mutation == "scope":proofs["offer_price"]["scope"] = "owned"
    if mutation == "rights":proofs["offer_price"]["rights_proven"] = True
    if mutation == "edition":proofs["cover_url"]["edition_proven"] = True
    if mutation == "unknown_field":fields["download_url"] = "PRIVATE"
    if mutation == "missing_evidence":proofs.pop("offer_price")
    d["public_commercial"] = commercial
    with pytest.raises(PublicCommercialError):
        validated_public_commercial(d, URL)
    with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
        merge_public_detail(commercial_row(), d)
    with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
        merge_attested_owned_public_detail(owned_row(), d)


def test_book_only_jsonld_without_observed_product_does_not_imply_offer():
    book = {
        "@type": "Book", "name": "Book", "url": URL,
        "offers": {
            "@type": "Offer", "price": "10.99", "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock",
        },
    }
    assert extract_public_commercial(book, URL) is None
