"""#1072: owned/public provenance must be checked at the merge boundary.

No account HTTP, purchases, downloads or ACSM. Uses the public HTML fixture
already in the repository and a synthetic purchased-library projection.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_public_listing
from cifereca_numilog_public import parse_public_search

SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
PAGE = parse_public_search(FIXTURE.read_text(), SOURCE)


def private_row():
    session = NumilogSession(
        credential_id="account-one", label="Owned", credentials={},
        options={}, config={}, diagnostics=None,
    )
    try:
        return session._normalize({
            "lid": "42", "variant": "()", "title": PAGE.items[0].title,
            "cover": None, "public_url": PAGE.items[0].friendly_url,
        })
    finally:
        session.close()


@pytest.mark.parametrize("changes", [
    {"owned": False},
    {"owned": None},
    {"source_collection": "public_catalog"},
    {"source_provider": "other-provider"},
    {"source_credential": "__public__"},
    {"source_credential": ""},
    {"downloadable": False},
    {"download_url": "https://example.invalid/private?token=PRIVATE"},
    {"metadata": {}},
    {"metadata": {"public_url_evidence": "caller_guessed"}},
])
def test_unverified_purchased_origin_is_rejected_before_copy(changes):
    owned = replace(private_row(), **changes)
    initial = deepcopy(owned.metadata)
    with pytest.raises(ProviderCapabilityError, match="owned_edition_evidence_unverified") as exc:
        merge_attested_public_listing(owned, PAGE.items)
    assert "PRIVATE" not in str(exc.value)
    assert owned.metadata == initial


@pytest.mark.parametrize("changes", [
    {"scenario": "NUMILOG_PRIVATE"},
    {"docbase": "OTHER"},
])
def test_public_rows_with_forged_scope_cannot_enrich_a_purchase(changes):
    owned = private_row()
    public = replace(PAGE.items[0], **changes)
    with pytest.raises(ProviderCapabilityError, match="public_edition_evidence_conflict"):
        merge_attested_public_listing(owned, (public,))
    assert "public_enrichment" not in owned.metadata


def test_authentic_private_and_public_proofs_still_enrich_only_metadata():
    owned = private_row()
    result, state = merge_attested_public_listing(owned, PAGE.items)
    assert state == "attested_listing_match"
    assert result.owned is True
    assert result.download_url is None
    assert result.source_credential == "account-one"
    assert result.metadata["public_enrichment"]["scope"] == "public_listing_only"
    assert result.format is None


@pytest.mark.parametrize("change", [
    "url", "record", "source_item", "source_item_only",
])
@pytest.mark.parametrize("valid_sibling", [False, True])
def test_partial_target_coordinates_are_conflicts_not_unrelated_cards(change, valid_sibling):
    owned = private_row()
    target, other = PAGE.items[:2]
    assert target.friendly_url != other.friendly_url
    assert target.record_id != other.record_id
    assert target.metadata["source_item_id"] != other.metadata["source_item_id"]
    if change == "url":
        bad = replace(target, friendly_url=other.friendly_url)
    elif change == "record":
        bad = replace(target, record_id=other.record_id)
    elif change == "source_item":
        bad = replace(target, metadata={
            **target.metadata, "source_item_id": other.metadata["source_item_id"],
        })
    else:
        bad = replace(other, metadata={
            **other.metadata, "source_item_id": target.metadata["source_item_id"],
        })
    candidates = (other, bad, target) if valid_sibling else (other, bad)
    before = deepcopy(owned.metadata)
    with pytest.raises(ProviderCapabilityError, match="public_edition_evidence_conflict"):
        merge_attested_public_listing(owned, candidates)
    assert owned.metadata == before


def test_unrelated_cards_without_target_coordinates_do_not_block_exact_match():
    owned = private_row()
    target, other = PAGE.items[:2]
    unchanged, status = merge_attested_public_listing(owned, (other,))
    assert unchanged is owned and status == "not_attested_on_first_page"
    enriched, status = merge_attested_public_listing(owned, (other, target))
    assert status == "attested_listing_match"
    assert enriched.record_id == owned.record_id
    assert enriched.download_url is None and enriched.format is None
