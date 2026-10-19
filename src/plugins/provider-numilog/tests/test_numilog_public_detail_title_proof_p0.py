"""P0 #1886: public product detail title is mandatory before sharing ISBN/offer.

Reduced observed anonymous Blackbone fixture, synthetic mutation of its
already parsed JSON-LD proof; no network, purchases, accounts or ACSM.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import (
    merge_public_detail, parse_public_detail_jsonld,
)

FIXTURES = Path(__file__).parent / "fixtures"
PRODUCT_URL = (
    "https://www.numilog.com/1171334/"
    "blackbone--coltan-song-tome-1--des-15-ans.ebook"
)
SOURCE_URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=loup"
)
PRODUCT = parse_public_detail_jsonld(
    (FIXTURES / "observed_public_blackbone.html").read_text(),
    PRODUCT_URL,
)


def public_card():
    base = parse_public_search(
        (FIXTURES / "observed_search-loup-0.html").read_text(),
        SOURCE_URL,
    ).items[0]
    return replace(
        base, title=PRODUCT["fields"]["title"],
        friendly_url=PRODUCT_URL, record_id="numilog:public:1171334",
        source_provider="numilog-catalog", source_credential="__public__",
        source_collection="public_catalog", owned=False, downloadable=False,
        download_url=None,
        metadata={"source_url": SOURCE_URL, "source_item_id": "1171334"},
    )


def detail_with_title(value, *, missing=False):
    detail = deepcopy(PRODUCT)
    if missing:
        detail["fields"].pop("title", None)
        detail["field_evidence"].pop("title", None)
    else:
        detail["fields"]["title"] = value
    return detail


@pytest.mark.parametrize("title", [
    "Autre édition de Blackbone", "PRIVATE_CATALOG_TITLE",
    "Blackbone tome 2", "", " ", None, ["Blackbone"],
    "Blackbone\nPRIVATE_LICENSE",
])
def test_conflicting_or_unattested_title_blocks_all_public_metadata(title):
    row = public_card()
    original = deepcopy(row.metadata)
    details = detail_with_title(title)
    with pytest.raises(ProviderCapabilityError) as caught:
        merge_public_detail(row, details)
    assert "PRIVATE_CATALOG_TITLE" not in str(caught.value)
    assert "PRIVATE_LICENSE" not in str(caught.value)
    assert row.metadata == original
    assert "public_detail" not in row.metadata
    assert "public_identifiers" not in row.metadata
    assert row.owned is False and row.downloadable is False


def test_absent_jsonld_title_cannot_attach_an_isbn_or_a_commercial_offer():
    row = public_card()
    details = detail_with_title(None, missing=True)
    assert details["public_identifiers"]["epub"] == "9782092591093"
    assert details["public_commercial"]["fields"]["offer_price"] == "10.99"
    with pytest.raises(ProviderCapabilityError, match="detail_title_conflict"):
        merge_public_detail(row, details)
    assert "public_detail" not in row.metadata


def test_stale_or_renamed_search_card_refuses_current_product_detail():
    row = replace(public_card(), title="Autre livre, même identifiant public")
    with pytest.raises(ProviderCapabilityError, match="detail_title_conflict"):
        merge_public_detail(row, deepcopy(PRODUCT))


def test_normalized_case_and_whitespace_match_still_keep_commercial_scope():
    row = replace(public_card(), title=(
        "  " + PRODUCT["fields"]["title"].upper().replace(" ", "   ") + "  "
    ))
    result = merge_public_detail(row, deepcopy(PRODUCT))
    assert result.record_id == row.record_id
    assert result.owned is False and result.downloadable is False
    assert result.download_url is None
    assert result.metadata["public_detail"]["public_identifiers"]["epub"] == "9782092591093"
    assert result.metadata["public_detail"]["public_commercial"]["fields"]["offer_price"] == "10.99"
    assert result.metadata["public_detail"]["rights_proven"] is False
    assert result.metadata["public_detail"]["format_edition_proven"] is False


def test_different_author_and_description_still_surface_conflicts_if_title_matches():
    row = public_card()
    details = deepcopy(PRODUCT)
    details["fields"]["authors"] = ["Auteur commercial différent"]
    details["fields"]["description"] = "Autre texte public attesté."
    result = merge_public_detail(row, details)
    assert result.record_id == row.record_id
    assert result.owned is False
    assert "title" not in result.metadata["public_detail"]["listing_conflicts"]
    assert result.metadata["public_detail"]["rights_proven"] is False
