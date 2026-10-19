"""#1072 P0 — source-attested editorial credits, never purchase rights.

The anonymous search HTML is a recorded fixture; all Book JSON-LD below is
synthetic. No network, account login, signed ACSM, fulfillment or extra GET.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_owned_direct_public_detail
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import (
    PublicDetailError, merge_public_detail, parse_public_detail_jsonld,
    validated_public_jsonld_fields,
)

FIX = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search(FIX.read_text(), SOURCE)


def details(index=0, **overrides):
    card = PAGE.items[index]
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "url": card.friendly_url, "name": card.title,
        "author": [{"@type": "Person", "name": "Autrice publiée"}],
        "translator": [{"@type": "Person", "name": "Traductrice attestée"},
                       {"@type": "Organization", "name": "Agence de traduction"}],
        "illustrator": {"@type": "Person", "name": "Illustrateur attesté"},
        "contributor": {"@type": "Person", "name": "Contributeur sans rôle précis"},
        "bookEdition": "Édition augmentée",
        "numberOfPages": 284,
        "typicalAgeRange": "12-15",
    }
    node.update(overrides)
    html = ('<script type="application/ld+json">'
            + json.dumps(node, ensure_ascii=False) + '</script>')
    return parse_public_detail_jsonld(html, card.friendly_url)


def purchased(index, *, credential="alice", lid="42"):
    card = PAGE.items[index]
    session = NumilogSession(
        credential_id=credential, label="Numilog",
        credentials={}, options={}, config={}, diagnostics=None,
    )
    session.connected = True
    owned = session._normalize({
        "lid": lid, "variant": "()", "title": card.title,
        "cover": None, "public_url": card.friendly_url,
    })
    return session, owned


def test_book_credits_and_literary_facts_are_bounded_with_exact_provenance():
    data = details()
    fields, proof = validated_public_jsonld_fields(data, PAGE.items[0].friendly_url)
    assert fields["authors"] == ["Autrice publiée"]
    assert fields["translators"] == ["Traductrice attestée", "Agence de traduction"]
    assert fields["illustrators"] == ["Illustrateur attesté"]
    assert fields["contributors"] == ["Contributeur sans rôle précis"]
    assert fields["public_edition_label"] == "Édition augmentée"
    assert fields["public_page_count"] == 284
    assert fields["public_age_range_label"] == "12-15"
    for key in ("translators", "illustrators", "contributors",
                "public_edition_label", "public_page_count",
                "public_age_range_label"):
        assert proof[key] == {
            "source": "public_product_jsonld_Book",
            "source_url": PAGE.items[0].friendly_url,
            "kind": "observed_public_jsonld",
            "edition_proven": False,
        }
    assert data["owned"] is False and data["downloadable"] is False
    assert data["download_url"] is None


def test_direct_owned_public_detail_preserves_roles_not_as_authors_or_rights():
    s, owned = purchased(0)
    try:
        result = merge_attested_owned_direct_public_detail(owned, details())
        evidence = result.metadata["public_detail_enrichment"]
        fields = evidence["public_jsonld"]["fields"]
        assert fields["translators"] == ["Traductrice attestée", "Agence de traduction"]
        assert fields["illustrators"] == ["Illustrateur attesté"]
        assert fields["public_page_count"] == 284
        assert evidence["public_jsonld"]["field_evidence"]["translators"]["source_url"] == owned.friendly_url
        assert result.author == "Autrice publiée"
        assert "Traductrice attestée" not in result.author
        assert result.title == owned.title
        assert result.owned is True and result.downloadable is True
        assert result.download_url is None and result.format is None
        assert result.metadata["format_unverified"] is True
        assert "recommended_age" not in result.metadata
        assert "isbn_digital" not in result.metadata
        assert "public_detail_enrichment" not in owned.metadata
        assert evidence["rights_proven_by_public_detail"] is False
        assert evidence["format_edition_proven"] is False
    finally:
        s.close()


def test_public_catalogue_projection_keeps_credits_without_promoting_rights():
    public = replace(PAGE.items[0], source_provider="numilog-catalog")
    result = merge_public_detail(public, details())
    fields = result.metadata["public_detail"]["fields"]
    assert fields["translators"] == ["Traductrice attestée", "Agence de traduction"]
    assert fields["public_edition_label"] == "Édition augmentée"
    assert result.owned is False and result.downloadable is False
    assert result.download_url is None
    assert result.format == public.format
    assert "recommended_age" not in result.metadata


def test_same_titled_products_keep_separate_credits_and_identifiers():
    assert PAGE.items[1].title == PAGE.items[2].title
    assert PAGE.items[1].friendly_url != PAGE.items[2].friendly_url
    results = []
    for index, person in ((1, "Traductrice PDF"), (2, "Traducteur EPUB")):
        session, owned = purchased(index, lid=str(index + 40))
        try:
            record = details(index, translator={"@type": "Person", "name": person})
            result = merge_attested_owned_direct_public_detail(owned, record)
            results.append(result)
        finally:
            session.close()
    assert results[0].record_id != results[1].record_id
    assert results[0].friendly_url != results[1].friendly_url
    assert (results[0].metadata["public_detail_enrichment"]["public_jsonld"]["fields"]["translators"]
            == ["Traductrice PDF"])
    assert (results[1].metadata["public_detail_enrichment"]["public_jsonld"]["fields"]["translators"]
            == ["Traducteur EPUB"])
    assert all(r.format is None and "isbn_digital" not in r.metadata for r in results)


@pytest.mark.parametrize("bad,expected_key", [
    ({"translator": "Nom libre non typé"}, "translators"),
    ({"translator": {"@type": ["Person"], "name": "Malformé"}}, "translators"),
    ({"translator": {"@type": "Book", "name": "Étranger"}}, "translators"),
    ({"translator": [{"@type": "Person", "name": "Valide"}, {"name": "PRIVATE\n"}]}, "translators"),
    ({"illustrator": {"@type": "Organization", "name": "Pas une personne"}}, "illustrators"),
    ({"contributor": [{"name": "Beaucoup"}] * 17}, "contributors"),
    ({"bookEdition": "Édition\u202ePRIVATE"}, "public_edition_label"),
    ({"typicalAgeRange": "PRIVATE\n12-15"}, "public_age_range_label"),
    ({"numberOfPages": True}, "public_page_count"),
    ({"numberOfPages": 20001}, "public_page_count"),
])
def test_malformed_optional_credits_do_not_pollute_public_or_owned_fields(bad, expected_key):
    record = details(**bad)
    assert expected_key not in record["fields"]
    assert expected_key not in record["field_evidence"]
    validated_public_jsonld_fields(record, PAGE.items[0].friendly_url)


@pytest.mark.parametrize("key,bad", [
    ("translators", ["PRIVATE\n"]),
    ("illustrators", ["x"] * 17),
    ("contributors", [{"name": "PRIVATE"}]),
    ("translators", ["Duplicata", "Duplicata"]),
    ("public_edition_label", "PRIVATE\u202e"),
    ("public_age_range_label", "x" * 81),
    ("public_page_count", True),
    ("public_page_count", 20001),
    ("public_page_count", "284"),
])
def test_mutated_cached_public_evidence_fails_closed_before_owned_projection(key, bad):
    record = details()
    forged = deepcopy(record)
    forged["fields"][key] = bad
    with pytest.raises(PublicDetailError, match="public_jsonld_evidence_unverified"):
        validated_public_jsonld_fields(forged, PAGE.items[0].friendly_url)
    s, owned = purchased(0)
    try:
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict") as caught:
            merge_attested_owned_direct_public_detail(owned, forged)
        assert "PRIVATE" not in str(caught.value)
    finally:
        s.close()


def test_contributor_role_proof_cannot_be_reattributed_to_other_product():
    record = details()
    forged = deepcopy(record)
    forged["field_evidence"]["translators"]["source_url"] = PAGE.items[1].friendly_url
    s, owned = purchased(0)
    try:
        with pytest.raises(ProviderCapabilityError, match="evidence_conflict"):
            merge_attested_owned_direct_public_detail(owned, forged)
    finally:
        s.close()
