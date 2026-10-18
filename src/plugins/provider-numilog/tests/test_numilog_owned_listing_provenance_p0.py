"""P0 #1072: authenticated-title vs public listing proof, without any ACSM.

Uses a sanitized, offline public Numilog fixture and synthetic owned cards.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_public_listing
from cifereca_numilog_public import parse_public_search

ROOT = Path(__file__).parent / "fixtures"
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search((ROOT / "public_search_jefferson_20261016.html").read_text(), SOURCE)


def purchased(index=0, *, title=None):
    card = PAGE.items[index]
    session = NumilogSession(
        credential_id="alice", label="Compte", credentials={},
        options={}, config={}, diagnostics=None,
    )
    try:
        return session._normalize({
            "lid": str(index + 42), "variant": "()",
            "title": card.title if title is None else title,
            "cover": None, "public_url": card.friendly_url,
        })
    finally:
        session.close()


def copy_card(index=0, **changes):
    original = PAGE.items[index]
    return replace(original, **changes)


def assert_private_metadata_not_changed(owned, result):
    assert result.record_id == owned.record_id
    assert result.owned is True and result.downloadable is True
    assert result.download_url is None
    assert result.price is None and result.currency is None
    assert result.format is None
    assert "isbn_digital" not in result.metadata


def test_exact_title_allows_proven_public_fields_but_not_purchase_price():
    owned = purchased()
    enriched, status = merge_attested_public_listing(owned, PAGE.items)
    assert status == "attested_listing_match"
    assert enriched.author == "Jean-Claude Mourlevat"
    assert enriched.publisher == "Gallimard Jeunesse"
    assert enriched.description and enriched.thumbnail_url
    assert enriched.metadata["public_offer_price"] == {
        "amount": "9.99", "minor_units": 999, "currency": "EUR",
        "applies_to": "displayed_card_offer",
    }
    assert_private_metadata_not_changed(owned, enriched)
    assert owned.author is None and "public_enrichment" not in owned.metadata


@pytest.mark.parametrize("private_title", [
    "Autre livre homonyme",
    "Jefferson prend des risques - Edition de poche",
    "Jefferson prend des risques PRIVATE_ACCOUNT",
])
def test_same_public_url_with_contradictory_owned_title_rejected(private_title):
    owned = purchased(title=private_title)
    with pytest.raises(ProviderCapabilityError, match="public_edition_title_conflict") as raised:
        merge_attested_public_listing(owned, (PAGE.items[0],))
    assert "PRIVATE_ACCOUNT" not in str(raised.value)
    assert owned.author is None and "public_offer_price" not in owned.metadata


def test_changed_public_title_under_same_url_cannot_project_metadata():
    owned = purchased()
    changed = copy_card(title="Jefferson — autre édition")
    with pytest.raises(ProviderCapabilityError, match="public_edition_title_conflict"):
        merge_attested_public_listing(owned, (changed,))


def test_only_unicode_case_and_whitespace_variants_preserve_title_equivalence():
    owned = purchased(title="  JEFFERSON  PREND  DES  RISQUES   ")
    enriched, status = merge_attested_public_listing(owned, (PAGE.items[0],))
    assert status == "attested_listing_match"
    assert enriched.title == owned.title
    assert_private_metadata_not_changed(owned, enriched)


@pytest.mark.parametrize(("evidence_field", "selector"), [
    ("title", "#some-other-card"),
    ("authors", "#private-account-name"),
    ("publisher", "#private-publisher"),
    ("published_at", "#random-date"),
    ("summary", "#footer"),
    ("cover_url", "#cover-of-other-book"),
    ("price", "#private-price"),
    ("collection", "#foreign-collection"),
])
def test_changed_field_selector_refused_before_provenance_copy(evidence_field, selector):
    owned = purchased()
    public = deepcopy(PAGE.items[0])
    proof = public.metadata["field_evidence"][evidence_field]
    proof["source"] = selector
    with pytest.raises(ProviderCapabilityError, match="public_edition_evidence_conflict") as raised:
        merge_attested_public_listing(owned, (public,))
    assert selector not in str(raised.value)


@pytest.mark.parametrize(("key", "value"), [
    ("amount", "999999.99"),
    ("minor_units", 111111),
    ("currency", "PRIVATE_CURRENCY"),
    ("raw_parts", ["1", "00", "€"]),
    ("raw_parts", ["1", "00", "PRIVATE_TOKEN"]),
    ("display_reconstructed", "PRIVATE_TOKEN"),
    ("applies_to", "purchased_epub"),
    ("tax_included", True),
    ("source_selectors", {"major": "PRIVATE_TOKEN"}),
])
def test_forged_commercial_price_never_survives_to_private_metadata(key, value):
    owned = purchased()
    public = deepcopy(PAGE.items[0])
    public.metadata["price"][key] = value
    with pytest.raises(ProviderCapabilityError, match="public_edition_evidence_conflict") as raised:
        merge_attested_public_listing(owned, (public,))
    assert "PRIVATE_TOKEN" not in str(raised.value)
    assert "public_offer_price" not in owned.metadata


def test_identical_title_two_product_ids_remain_distinct_not_purchased_format():
    pdf = purchased(1)
    epub = purchased(2)
    assert pdf.title == epub.title and pdf.friendly_url != epub.friendly_url
    pdf_result, pdf_status = merge_attested_public_listing(pdf, PAGE.items)
    epub_result, epub_status = merge_attested_public_listing(epub, PAGE.items)
    assert pdf_status == epub_status == "attested_listing_match"
    assert pdf_result.metadata["public_enrichment"]["product_url"] == pdf.friendly_url
    assert epub_result.metadata["public_enrichment"]["product_url"] == epub.friendly_url
    assert_private_metadata_not_changed(pdf, pdf_result)
    assert_private_metadata_not_changed(epub, epub_result)


def test_no_public_match_still_returns_owned_without_claims():
    owned = purchased()
    unrelated = (PAGE.items[1], PAGE.items[2])
    enriched, status = merge_attested_public_listing(owned, unrelated)
    assert status == "not_attested_on_first_page"
    assert enriched is owned
    assert "public_enrichment" not in owned.metadata


def test_private_title_unicode_controls_are_rejected_without_echo():
    owned = purchased(title="token=PRIVATE\n")
    with pytest.raises(ProviderCapabilityError, match="title_conflict") as exc:
        merge_attested_public_listing(owned, (PAGE.items[0],))
    assert "PRIVATE" not in str(exc.value)
