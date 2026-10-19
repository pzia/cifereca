"""#1886 P0 — bound adversarial product HTML before recursive visible scans.

Fixture is public and sanitized; generated HTML is synthetic. No HTTP,
account session, fulfillment link, acquisition or purchase.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public_metadata import (
    PublicMetadataError, extract_public_metadata,
)
from cifereca_numilog_html import Parser

ROOT = Path(__file__).parent / "fixtures"
URL = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
TITLE = "Blackbone - Coltan song- Tome 1 - Dès 15 ans"


def sample():
    return (ROOT / "observed_metadata_blackbone.html").read_text()


def test_recorded_product_metadata_still_qualifies_after_dom_budget():
    record = extract_public_metadata(sample(), URL, TITLE)
    assert record["fields"]["brand"] == "NATHAN"
    assert record["fields"]["pagination_label"] == "228 pages"
    assert record["fields"]["language_label"] == "Français"
    assert record["product_url"] == URL
    assert all(proof["rights_proven"] is False
               and proof["edition_proven"] is False
               for proof in record["field_evidence"].values())


@pytest.mark.parametrize("depth", [81, 95])
def test_deep_markup_is_rejected_with_fixed_message_not_recursion(depth):
    html = sample().replace(
        "</body>", "<div>" * depth + "</div>" * depth + "</body>"
    )
    assert len(html.encode("utf-8")) < 1024 * 1024
    # The generic HTML parser MUST accept this sample. Otherwise only its
    # existing 25k-node / 100-level guard would be tested, not our new bound.
    assert Parser(html).count < 25000
    with pytest.raises(PublicMetadataError) as caught:
        extract_public_metadata(html, URL, TITLE)
    assert str(caught.value) == "Numilog public metadata: evidence_unverified"


def test_many_sibling_nodes_are_rejected_even_under_http_byte_budget():
    html = sample().replace(
        "</body>", "<span></span>" * 12500 + "</body>"
    )
    assert len(html.encode("utf-8")) < 1024 * 1024
    # The generic HTML parser MUST accept this sample. Otherwise only its
    # existing 25k-node / 100-level guard would be tested, not our new bound.
    assert Parser(html).count < 25000
    with pytest.raises(PublicMetadataError) as caught:
        extract_public_metadata(html, URL, TITLE)
    assert str(caught.value) == "Numilog public metadata: evidence_unverified"


def test_missing_html_wrapper_does_not_turn_into_purchased_book_rights():
    response = extract_public_metadata("<h1>Unknown product</h1>", URL, TITLE)
    assert response == {
        "product_url": URL, "fields": {}, "field_evidence": {},
    }
