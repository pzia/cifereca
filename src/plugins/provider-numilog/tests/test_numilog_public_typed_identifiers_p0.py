"""Numilog P0 #1072: explicitly labelled paper/EPUB/PDF identifiers, offline.

Example identifier values are from public Numilog product-page text reviewed
on 2026-10-17, but ALL HTML fixtures below are synthetic. No live HTTP,
cookies, personal-library exports, signed delivery URLs, or fulfillment.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import (
    merge_attested_public_listing, merge_attested_owned_public_detail,
)
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import (
    parse_public_detail_jsonld, PublicDetailError, merge_public_detail,
)
from cifereca_numilog_public_identifiers import (
    extract_public_identifiers, PublicIdentifierError,
)

BLACKBONE = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
SOLINE = "https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook"
FIX = Path(__file__).with_name("fixtures")
CAPTURE = (FIX / "public_search_jefferson_20261016.html").read_text()
SOURCE = json.loads((FIX / "public_search_jefferson_20261016.expected.json").read_text())["source_url"]
SEARCH = parse_public_search(CAPTURE, SOURCE)


def product_html(url, title, visible):
    book = json.dumps({"@context": "https://schema.org", "@type": "Book",
                       "url": url, "name": title}, ensure_ascii=False)
    return ("<html><h1>" + title + "</h1><h2>Détails</h2>"
            + visible + '<script type="application/ld+json">' + book
            + "</script></html>")


def test_public_blackbone_paper_and_epub_no_drm_are_distinct():
    html = product_html(BLACKBONE, "Blackbone", (
        "<dt>EAN papier</dt><dd>9782092591086</dd>"
        "<h3>Informations ebook</h3>"
        "<dt>EAN EPUB SANS DRM</dt><dd>9782092591093</dd>"
        "<aside><p>Guide des formats</p><p>DRM Adobe, LCP, EPUB, PDF</p></aside>"
    ))
    result = parse_public_detail_jsonld(html, BLACKBONE)
    assert result["public_identifiers"] == {
        "paper": "9782092591086", "epub": "9782092591093",
    }
    proof = result["identifier_evidence"]
    assert proof["paper"]["public_offer_format"] is None
    assert proof["epub"]["public_offer_format"] == "epub"
    assert proof["epub"]["public_offer_drm_label"] == "sans_drm"
    assert all(p["owned_format_proven"] is False for p in proof.values())
    assert result["owned"] is False and result["downloadable"] is False
    assert result["download_url"] is None


def test_soline_generic_digital_identifier_is_not_an_epub_ean():
    html = product_html(SOLINE, "Le Piège de Sainte-Soline", (
        "<div>ISBN papier : 978-2-38035-452-2</div>"
        "<div>ISBN numérique : 978-2-38035-453-9</div>"
    ))
    result = parse_public_detail_jsonld(html, SOLINE)
    assert result["public_identifiers"] == {
        "paper": "9782380354522", "digital_unspecified": "9782380354539",
    }
    assert "epub" not in result["public_identifiers"]


def test_script_form_and_generic_format_guide_do_not_prove_identifiers():
    html = product_html(BLACKBONE, "Blackbone", (
        '<script>const raw="EAN EPUB SANS DRM: 9782092591093";</script>'
        "<form><label>EAN papier</label><input value='9782092591086'></form>"
        "<p>PDF / EPUB / DRM Adobe / LCP sans EAN</p>"
    ))
    assert parse_public_detail_jsonld(html, BLACKBONE)["public_identifiers"] == {}


def test_product_details_marker_is_required_for_visible_label_scan():
    html = "<div>EAN EPUB SANS DRM: 9782092591093</div>"
    assert extract_public_identifiers(html, BLACKBONE)["identifiers"] == {}


def test_two_different_explicit_epub_eans_fail_closed():
    html = product_html(BLACKBONE, "Blackbone", (
        "<dt>EAN EPUB</dt><dd>9782092591093</dd>"
        "<dt>EAN EPUB</dt><dd>9782092591086</dd>"
    ))
    with pytest.raises(PublicDetailError, match="public_identifier_evidence_unverified"):
        parse_public_detail_jsonld(html, BLACKBONE)


def test_invalid_isbn_checksum_never_published():
    html = product_html(BLACKBONE, "Blackbone", (
        "<div>EAN papier</div><div>9782092591087</div>"
    ))
    assert parse_public_detail_jsonld(html, BLACKBONE)["public_identifiers"] == {}


def test_merge_public_listing_retains_typed_identifiers_as_commercial_only():
    row = replace(SEARCH.items[0], source_provider="numilog-catalog")
    html = product_html(row.friendly_url, row.title, (
        "<p>EAN papier</p><p>9782092591086</p>"
        "<p>EAN EPUB SANS DRM</p><p>9782092591093</p>"
    ))
    detail = parse_public_detail_jsonld(html, row.friendly_url)
    merged = merge_public_detail(row, detail)
    assert merged.owned is False and merged.downloadable is False
    assert merged.download_url is None
    assert merged.format == row.format
    assert merged.metadata["public_detail"]["public_identifiers"]["paper"] == "9782092591086"
    assert merged.metadata["public_detail"]["public_identifiers"]["epub"] == "9782092591093"
    assert merged.metadata["public_detail"]["format_edition_proven"] is False
    assert "isbn_digital" not in merged.metadata


def test_merge_owned_keeps_format_and_rights_from_account_only():
    session = NumilogSession(
        credential_id="alice", label="Numilog", credentials={}, options={},
        config={}, diagnostics=None,
    )
    try:
        row = {"lid": "42", "variant": "()", "title": SEARCH.items[0].title,
               "cover": None, "public_url": SEARCH.items[0].friendly_url}
        owned = session._normalize(row)
        listing, status = merge_attested_public_listing(owned, [SEARCH.items[0]])
        assert status == "attested_listing_match"
        html = product_html(owned.friendly_url, owned.title, (
            "<dt>EAN papier</dt><dd>9782092591086</dd>"
            "<dt>EAN EPUB SANS DRM</dt><dd>9782092591093</dd>"
        ))
        detailed = merge_attested_owned_public_detail(
            listing, parse_public_detail_jsonld(html, owned.friendly_url),
        )
        assert detailed.owned is True and detailed.downloadable is True
        assert detailed.download_url is None and detailed.format is None
        assert detailed.metadata["format_unverified"] is True
        public_proof = detailed.metadata["public_detail_enrichment"]
        assert public_proof["public_identifiers"]["paper"] == "9782092591086"
        assert public_proof["public_identifiers"]["epub"] == "9782092591093"
        assert public_proof["format_edition_proven"] is False
        assert public_proof["rights_proven_by_public_detail"] is False
        assert "isbn_digital" not in detailed.metadata
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"source_url": "https://evil.test/PRIVATE"},
    {"owned_format_proven": True},
    {"public_offer_format": "epub"},
    {"public_offer_drm_label": "avec_drm"},
])
def test_forged_paper_evidence_rejected_before_merge(change):
    row = replace(SEARCH.items[0], source_provider="numilog-catalog")
    html = product_html(row.friendly_url, row.title,
                        "<dt>EAN papier</dt><dd>9782092591086</dd>")
    detail = parse_public_detail_jsonld(html, row.friendly_url)
    detail["identifier_evidence"]["paper"].update(change)
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        merge_public_detail(row, detail)


def test_bounded_html_cannot_be_exhausted_by_large_detail():
    with pytest.raises(PublicIdentifierError, match="html_too_large"):
        extract_public_identifiers("x" * (1024 * 1024 + 1), BLACKBONE)
