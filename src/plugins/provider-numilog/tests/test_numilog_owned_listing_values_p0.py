"""P0 #1072: observed selector alone does not validate mutated public values.

Synthetic purchased record plus previously attested, sanitized Jefferson HTML.
No authenticated requests, ACSM, signed URL requests or purchases.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_public_listing
from cifereca_numilog_public import parse_public_search

SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search(
    (Path(__file__).parent / "fixtures" /
     "public_search_jefferson_20261016.html").read_text(), SOURCE,
)
PRIVATE = (
    "TOKEN_PRIVATE_VALUE",
    "https://attacker.invalid/cover.jpg?token=PRIVATE",
    "alice@example.invalid\n",
)


def owned():
    original = PAGE.items[0]
    session = NumilogSession(
        credential_id="alice", label="Achats", credentials={},
        options={}, config={}, diagnostics=None,
    )
    try:
        return session._normalize({
            "lid": "42", "variant": "()", "title": original.title,
            "cover": None, "public_url": original.friendly_url,
        })
    finally:
        session.close()


def tampered(*, field=None, value=None, metadata_patch=None):
    public = deepcopy(PAGE.items[0])
    metadata = deepcopy(public.metadata)
    if metadata_patch:
        metadata_patch(metadata)
    if field is not None:
        return replace(public, **{field: value}, metadata=metadata)
    return replace(public, metadata=metadata)


def reject(public):
    private = owned()
    before = deepcopy(private.metadata)
    with pytest.raises(ProviderCapabilityError, match="public_edition_value_unverified") as raised:
        merge_attested_public_listing(private, (public,))
    published = str(raised.value)
    for sensitive in PRIVATE:
        assert sensitive not in published
    assert private.metadata == before
    assert private.author is None and private.description is None


@pytest.mark.parametrize(("field", "bad_value"), [
    ("author", ["Alice"]),
    ("author", {"value": "Alice"}),
    ("author", "Alice ; TOKEN_PRIVATE_VALUE"),
    ("author", "A"*33000),
    ("author", "alice@example.invalid\n"),
    ("publisher", ["Publisher"]),
    ("publisher", "P"*2001),
    ("publisher", "alice@example.invalid\n"),
    ("description", {"secret": "TOKEN_PRIVATE_VALUE"}),
    ("description", "D"*16001),
    ("description", "description\x00TOKEN_PRIVATE_VALUE"),
    ("published_at", "2026-02-30"),
    ("published_at", "2026-10-08\n"),
    ("published_at", ["2026-10-08"]),
    ("published_at", "TOKEN_PRIVATE_VALUE"),
    ("thumbnail_url", "https://attacker.invalid/cover.jpg?token=PRIVATE"),
    ("thumbnail_url", "http://couverture.numilog.com/42_GRANDE.jpg"),
    ("thumbnail_url", "https://couverture.numilog.com/42_GRANDE.jpg?session=PRIVATE"),
    ("thumbnail_url", "https://couverture.numilog.com@attacker.invalid/42_GRANDE.jpg"),
    ("thumbnail_url", {"src": "TOKEN_PRIVATE_VALUE"}),
])
def test_mutated_public_values_cannot_poison_purchased_record(field, bad_value):
    reject(tampered(field=field, value=bad_value))


@pytest.mark.parametrize("names", [
    None,
    [],
    ["Jean-Claude Mourlevat", "alice@example.invalid\n"],
    ["Jean-Claude Mourlevat", "TOKEN_PRIVATE_VALUE"],
    ["TOKEN_PRIVATE_VALUE"],
    "Jean-Claude Mourlevat",
    [42],
    ["A"*1001],
    ["Nom"]*33,
])
def test_public_author_proof_requires_exact_attested_author_list(names):
    def patch(meta):
        meta["authors"] = names
    reject(tampered(metadata_patch=patch))


@pytest.mark.parametrize("collection", [
    "alice@example.invalid\n",
    "C"*2001,
    ["TOKEN_PRIVATE_VALUE"],
    {"private": "TOKEN_PRIVATE_VALUE"},
])
def test_unqualified_commercial_collection_not_copied_into_purchase(collection):
    def patch(meta):
        meta["collection"] = collection
    reject(tampered(metadata_patch=patch))


def test_valid_observed_values_remain_accepted_and_original_unchanged():
    purchased = owned()
    original = deepcopy(purchased.metadata)
    updated, state = merge_attested_public_listing(purchased, (PAGE.items[0],))
    assert state == "attested_listing_match"
    assert updated.author == "Jean-Claude Mourlevat"
    assert updated.publisher == "Gallimard Jeunesse"
    assert updated.published_at == "2026-10-08"
    assert updated.description and updated.thumbnail_url
    assert updated.metadata["public_enrichment"]["status"] == "attested_listing_match"
    assert updated.metadata["public_offer_price"]["currency"] == "EUR"
    assert purchased.metadata == original


def test_valid_source_without_optional_collection_remains_supported():
    def patch(metadata):
        metadata["collection"] = None
    result, state = merge_attested_public_listing(
        owned(), (tampered(metadata_patch=patch),),
    )
    assert state == "attested_listing_match"
    assert "public_collection" not in result.metadata


def test_non_matching_public_product_remains_no_op_without_validating_foreign_fields():
    purchased = owned()
    foreign = replace(
        PAGE.items[1],
        description="TOKEN_PRIVATE_VALUE",
        thumbnail_url="https://attacker.invalid/cover.jpg?token=PRIVATE",
    )
    same, state = merge_attested_public_listing(purchased, (foreign,))
    assert state == "not_attested_on_first_page"
    assert same is purchased
