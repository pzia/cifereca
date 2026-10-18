"""#1072: retain complete, source-proven public Book fields for owned editions.

Offline tests only; the catalogue HTML is a sanitized public capture and the
product Book JSON-LD is synthetic. No login, delivery URL, ACSM or network.
"""
from copy import deepcopy
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
from cifereca_numilog_public_detail import parse_public_detail_jsonld

HERE = Path(__file__).parent / "fixtures"
SOURCE = json.loads((HERE / "public_search_jefferson_20261016.expected.json").read_text())["source_url"]
PAGE = parse_public_search((HERE / "public_search_jefferson_20261016.html").read_text(), SOURCE)


def session():
    s = NumilogSession(credential_id="alice", label="", credentials={},
                       options={}, config={}, diagnostics=None)
    s.connected = True
    return s


def purchased(s, card=0, *, lid="42"):
    listing = PAGE.items[card]
    private = s._normalize({
        "lid": lid, "variant": "()", "title": listing.title,
        "cover": None, "public_url": listing.friendly_url,
    })
    owned, status = merge_attested_public_listing(private, PAGE.items)
    assert status == "attested_listing_match"
    assert owned.owned is True and owned.downloadable is True
    assert owned.download_url is None and owned.format is None
    return owned


def detail(owned, description, **others):
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "name": owned.title, "url": owned.friendly_url,
        "author": [{"name": "Auteur public"}],
        "publisher": {"name": "Editeur public"},
        "description": description,
        "inLanguage": "fr", "isbn": "9782075241939",
    }
    node.update(others)
    html = '<script type="application/ld+json">' + json.dumps(node) + '</script>'
    return parse_public_detail_jsonld(html, owned.friendly_url)


def test_full_public_synopsis_extending_proven_excerpt_replaces_only_excerpt():
    s = session()
    try:
        owned = purchased(s)
        excerpt = owned.description
        assert excerpt and owned.metadata["public_enrichment"]["field_evidence"]["description"]["selector"] == "p.list-group-item-text"
        full = excerpt + " Chapitre supplémentaire, précisions éditoriales."
        result = merge_attested_owned_public_detail(owned, detail(owned, full))
        assert result.description == full
        assert owned.description == excerpt  # Immutable input.
        proof = result.metadata["public_detail_enrichment"]
        assert proof["replaced_listing_excerpt"] == excerpt
        assert proof["field_evidence"]["description"] == {
            "kind": "numilog.public_product_jsonld",
            "source_url": owned.friendly_url, "field": "description",
        }
        assert proof["public_jsonld"]["fields"]["description"] == full
        assert proof["public_jsonld"]["field_evidence"]["description"]["source_url"] == owned.friendly_url
        assert proof["public_jsonld"]["edition_proven"] is False
        assert proof["public_jsonld"]["rights_proven"] is False
        assert result.owned and result.downloadable and result.download_url is None
        assert result.format is None and result.price is None and result.currency is None
        assert "isbn_digital" not in result.metadata
    finally:
        s.close()


def test_unrelated_public_synopsis_does_not_replace_listing_but_remains_accessible():
    s = session()
    try:
        owned = purchased(s)
        alternative = "Autre texte public qui ne correspond pas à l'extrait de la carte."
        result = merge_attested_owned_public_detail(owned, detail(owned, alternative))
        assert result.description == owned.description
        proof = result.metadata["public_detail_enrichment"]
        assert "replaced_listing_excerpt" not in proof
        assert "description" in proof["listing_conflicts"]
        assert proof["public_jsonld"]["fields"]["description"] == alternative
        assert proof["public_jsonld"]["field_evidence"]["description"]["kind"] == "observed_public_jsonld"
        assert proof["public_jsonld"]["fields"]["authors"] == ["Auteur public"]
        assert proof["public_jsonld"]["fields"]["publisher"] == "Editeur public"
        assert proof["public_jsonld"]["fields"]["language"] == "fr"
        assert proof["public_jsonld"]["fields"]["isbn_edition_unspecified"] == "9782075241939"
        assert proof["rights_proven_by_public_detail"] is False
    finally:
        s.close()


def test_forged_or_missing_excerpt_proof_cannot_authorize_overwrite():
    s = session()
    try:
        owned = purchased(s)
        d = detail(owned, owned.description + " Les détails supplémentaires.")
        for bad_proof in [None, {}, {
            "kind": "numilog.public_search_listing",
            "source_url": "https://evil.invalid/PRIVATE",
            "selector": "p.list-group-item-text",
        }, {
            "kind": "numilog.public_search_listing",
            "source_url": SOURCE,
            "selector": "#private-or-unknown",
        }]:
            metadata = deepcopy(owned.metadata)
            if bad_proof is None:
                metadata["public_enrichment"]["field_evidence"].pop("description")
            else:
                metadata["public_enrichment"]["field_evidence"]["description"] = bad_proof
            altered = replace(owned, metadata=metadata)
            result = merge_attested_owned_public_detail(altered, d)
            assert result.description == owned.description
            assert "description" in result.metadata["public_detail_enrichment"]["listing_conflicts"]
            assert "replaced_listing_excerpt" not in result.metadata["public_detail_enrichment"]
    finally:
        s.close()


@pytest.mark.parametrize("bad", [
    {"description": "PRIVATE_MARKER\ncontrol"},  # hidden/control text
    {"description": "PRIVATE_MARKER" * 2000},  # unbounded body
    {"title": "PRIVATE_MARKER" * 70},
    {"authors": ["PRIVATE_MARKER\n"]},
    {"authors": []},
    {"language": "PRIVATE_MARKER\n"},
    {"isbn_edition_unspecified": "9781234567890"},
    {"published_at": "2026-99-99"},
])
def test_forged_jsonld_values_fail_closed_without_private_echo(bad):
    s = session()
    try:
        owned = purchased(s)
        d = detail(owned, owned.description + " Suite.")
        # Simulate a malformed caller bypassing the bounded HTML parser.
        for key, value in bad.items():
            d["fields"][key] = value
            d["field_evidence"][key] = {
                "source": "public_product_jsonld_Book", "source_url": owned.friendly_url,
                "kind": "observed_public_jsonld", "edition_proven": False,
            }
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict") as ex:
            merge_attested_owned_public_detail(owned, d)
        assert "PRIVATE_MARKER" not in str(ex.value)
    finally:
        s.close()


def test_same_title_different_products_keep_separate_proofs_and_unverified_formats():
    s = session()
    try:
        pdf = purchased(s, 1, lid="42")
        epub = purchased(s, 2, lid="43")
        assert pdf.title == epub.title and pdf.friendly_url != epub.friendly_url
        pdf_result = merge_attested_owned_public_detail(pdf, detail(pdf, pdf.description + " PDF notes."))
        epub_result = merge_attested_owned_public_detail(epub, detail(epub, epub.description + " EPUB notes."))
        assert pdf_result.description.endswith("PDF notes.")
        assert epub_result.description.endswith("EPUB notes.")
        assert pdf_result.metadata["public_detail_enrichment"]["product_url"] == pdf.friendly_url
        assert epub_result.metadata["public_detail_enrichment"]["product_url"] == epub.friendly_url
        assert pdf_result.owned and epub_result.owned
        assert pdf_result.format is None and epub_result.format is None
        assert pdf_result.download_url is None and epub_result.download_url is None
        assert "isbn_digital" not in pdf_result.metadata
        assert "isbn_digital" not in epub_result.metadata
    finally:
        s.close()


def test_public_product_title_mismatch_still_blocks_every_field_and_synopsis():
    s = session()
    try:
        owned = purchased(s)
        d = detail(owned, owned.description + " Suite.", name="Autre livre")
        with pytest.raises(ProviderCapabilityError, match="title_conflict"):
            merge_attested_owned_public_detail(owned, d)
    finally:
        s.close()
