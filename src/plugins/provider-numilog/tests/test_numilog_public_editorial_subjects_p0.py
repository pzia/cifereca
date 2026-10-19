"""#1886 / #1072 — Book editorial topics, series and alternate headline.

Recorded public search HTML; all optional JSON-LD fields are SYNTHETIC.
No HTTP, paid files, entitlement, identity service or authentication.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_owned_direct_public_detail
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import (
    PublicDetailError, merge_public_detail, parse_public_detail_jsonld,
    validated_public_jsonld_fields,
)

ROOT = Path(__file__).parent / "fixtures"
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search((ROOT / "public_search_jefferson_20261016.html").read_text(),
                           SOURCE)


def detail(index=0, **extra):
    item = PAGE.items[index]
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "url": item.friendly_url, "name": item.title,
        "genre": ["Aventure", "Roman jeunesse", "Aventure"],
        "keywords": "forêt, enquête, amitié",
        "alternativeHeadline": "Une enquête de Jefferson",
        "isPartOf": {"@type": "BookSeries", "name": "Les enquêtes de Jefferson"},
        "isbn": "9782075241939",
    }
    node.update(extra)
    return parse_public_detail_jsonld(
        '<script type="application/ld+json">'
        + json.dumps(node, ensure_ascii=False) + '</script>',
        item.friendly_url,
    )


def owned(index=0, *, lid="42"):
    item = PAGE.items[index]
    s = NumilogSession(credential_id="alice", label="", credentials={},
                       options={}, config={}, diagnostics=None)
    s.connected = True
    row = s._normalize({
        "lid": lid, "variant": "()", "title": item.title,
        "cover": None, "public_url": item.friendly_url,
    })
    return s, row


def test_source_literal_genres_keywords_and_series_keep_product_proof():
    data = detail()
    fields, proof = validated_public_jsonld_fields(data, PAGE.items[0].friendly_url)
    assert fields["genres"] == ["Aventure", "Roman jeunesse"]
    assert fields["public_keywords"] == ["forêt, enquête, amitié"]
    assert fields["public_series_label"] == "Les enquêtes de Jefferson"
    assert fields["public_alternative_headline"] == "Une enquête de Jefferson"
    assert "subtitle" not in fields
    assert "isbn_digital" not in fields
    for key in ("genres", "public_keywords", "public_series_label",
                "public_alternative_headline"):
        assert proof[key] == {
            "source": "public_product_jsonld_Book",
            "source_url": PAGE.items[0].friendly_url,
            "kind": "observed_public_jsonld",
            "edition_proven": False,
        }


def test_attested_owned_product_retains_genres_but_not_purchased_format():
    s, item = owned()
    try:
        result = merge_attested_owned_direct_public_detail(item, detail())
        evidence = result.metadata["public_detail_enrichment"]
        fields = evidence["public_jsonld"]["fields"]
        assert fields["genres"] == ["Aventure", "Roman jeunesse"]
        assert fields["public_series_label"] == "Les enquêtes de Jefferson"
        assert fields["public_keywords"] == ["forêt, enquête, amitié"]
        assert evidence["public_jsonld"]["field_evidence"]["genres"]["source_url"] == item.friendly_url
        assert result.record_id == item.record_id
        assert result.source_credential == item.source_credential
        assert result.owned is True and result.downloadable is True
        assert result.download_url is None and result.format is None
        assert result.price is None and result.currency is None
        assert "isbn_digital" not in result.metadata
        assert "recommended_age" not in result.metadata
        assert evidence["format_edition_proven"] is False
        assert evidence["rights_proven_by_public_detail"] is False
        assert "public_detail_enrichment" not in item.metadata
    finally:
        s.close()


def test_public_catalogue_remains_unowned_with_product_genres():
    card = replace(PAGE.items[0], source_provider="numilog-catalog")
    result = merge_public_detail(card, detail())
    fields = result.metadata["public_detail"]["fields"]
    assert fields["genres"] == ["Aventure", "Roman jeunesse"]
    assert fields["public_series_label"] == "Les enquêtes de Jefferson"
    assert result.owned is False and result.downloadable is False
    assert result.download_url is None
    # The listing carries the explicit sentinel None for digital ISBN.
    # Its anonymous product enrichment must not turn that into a right.
    assert result.metadata["isbn_digital"] is None


def test_two_homonymous_offers_keep_distinct_series_and_no_format_entitlement():
    assert PAGE.items[1].title == PAGE.items[2].title
    result = []
    for index, series in ((1, "Série PDF"), (2, "Série EPUB")):
        s, item = owned(index, lid=str(index + 40))
        try:
            info = detail(index, isPartOf={
                "@type": "https://schema.org/CreativeWorkSeries",
                "name": series,
            })
            result.append(merge_attested_owned_direct_public_detail(item, info))
        finally:
            s.close()
    assert result[0].record_id != result[1].record_id
    assert result[0].friendly_url != result[1].friendly_url
    assert [row.metadata["public_detail_enrichment"]["public_jsonld"]["fields"]["public_series_label"]
            for row in result] == ["Série PDF", "Série EPUB"]
    assert all(row.format is None and row.download_url is None for row in result)


@pytest.mark.parametrize("change,missing", [
    ({"genre": {"@type": "Thing", "name": "Une catégorie"}}, "genres"),
    ({"genre": ["Valide", 22]}, "genres"),
    ({"genre": ["Valide"] * 17}, "genres"),
    ({"genre": "https://evil.test/acquisition"}, "genres"),
    ({"genre": "PRIVATE\nSECRET"}, "genres"),
    ({"keywords": {"unsafe": "PRIVATE"}}, "public_keywords"),
    ({"keywords": "javascript:PRIVATE"}, "public_keywords"),
    ({"keywords": ["Livre", "PRIVATE\u202eHIDDEN"]}, "public_keywords"),
    ({"keywords": ["mot"] * 17}, "public_keywords"),
    ({"alternativeHeadline": ["Une série"]}, "public_alternative_headline"),
    ({"alternativeHeadline": "https://external.test/PRIVATE"}, "public_alternative_headline"),
    ({"alternativeHeadline": "PRIVATE\nSECRET"}, "public_alternative_headline"),
    ({"isPartOf": "Nom de série sans type"}, "public_series_label"),
    ({"isPartOf": {"name": "Série sans type"}}, "public_series_label"),
    ({"isPartOf": {"@type": "BookSeries", "name": "javascript:PRIVATE"}}, "public_series_label"),
    ({"isPartOf": {"@type": "Book", "name": "Autre livre"}}, "public_series_label"),
    ({"isPartOf": {"@type": ["BookSeries"], "name": "Type multiple"}}, "public_series_label"),
    ({"isPartOf": {"@type": "BookSeries", "name": "PRIVATE\u202e"}}, "public_series_label"),
])
def test_malformed_or_untyped_book_fields_are_omitted_not_assumed(change, missing):
    data = detail(**change)
    assert missing not in data["fields"]
    assert missing not in data["field_evidence"]
    validated_public_jsonld_fields(data, PAGE.items[0].friendly_url)


@pytest.mark.parametrize("field,value", [
    ("genres", ["PRIVATE\n"]),
    ("genres", ["x" * 257]),
    ("genres", ["http://attacker.invalid/PRIVATE"]),
    ("genres", ["Doubled", "Doubled"]),
    ("genres", ["genre"] * 17),
    ("public_keywords", "not a list"),
    ("public_keywords", ["javascript:PRIVATE"]),
    ("public_series_label", {"name": "PRIVATE"}),
    ("public_series_label", "PRIVATE\u202e"),
    ("public_series_label", "https://external.test/PRIVATE"),
    ("public_alternative_headline", "data:PRIVATE"),
    ("public_alternative_headline", "PRIVATE\n"),
])
def test_mutated_public_cache_fails_closed_without_leaking_title_or_identity(field, value):
    forged = deepcopy(detail())
    forged["fields"][field] = value
    with pytest.raises(PublicDetailError, match="evidence_unverified"):
        validated_public_jsonld_fields(forged, PAGE.items[0].friendly_url)
    s, item = owned()
    try:
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict") as caught:
            merge_attested_owned_direct_public_detail(item, forged)
        assert "PRIVATE" not in str(caught.value)
    finally:
        s.close()


def test_edition_metadata_is_not_accepted_from_another_product():
    forged = deepcopy(detail())
    forged["field_evidence"]["genres"]["source_url"] = PAGE.items[1].friendly_url
    s, item = owned()
    try:
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_direct_public_detail(item, forged)
    finally:
        s.close()
