"""P0 #1072/#1886: Numilog JSON-LD GTIN-13 is public, not owned-edition proof.

Synthetic JSON-LD and owned-card fixtures only: no HTTP, ACSM or account.
"""
import json
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_public_detail import (
    parse_public_detail_jsonld, validated_public_jsonld_fields, PublicDetailError,
)
from cifereca_numilog_enrichment import merge_attested_owned_direct_public_detail


URL = "https://www.numilog.com/42/fiche-test.ebook"
EAN = "9782092591093"
OTHER = "9782075241939"


def detail(**props):
    node = {"@type": "Book", "name": "Fiche test", "url": URL, **props}
    html = '<script type="application/ld+json">' + json.dumps(node) + "</script>"
    return parse_public_detail_jsonld(html, URL)


def test_observed_gtin13_is_public_metadata_without_epub_isbn_or_rights():
    result = detail(gtin13=EAN)
    assert result["fields"]["public_gtin13_edition_unspecified"] == EAN
    assert "isbn_edition_unspecified" not in result["fields"]
    assert "isbn_digital" not in result["fields"]
    assert result["owned"] is False and result["downloadable"] is False
    fields, evidence = validated_public_jsonld_fields(result, URL)
    assert fields["public_gtin13_edition_unspecified"] == EAN
    assert evidence["public_gtin13_edition_unspecified"] == {
        "source": "public_product_jsonld_Book",
        "source_url": URL, "kind": "observed_public_jsonld",
        "edition_proven": False,
    }


def test_isbn_and_gtin13_disagree_without_fake_digital_matching():
    result = detail(isbn=OTHER, gtin13=EAN)
    assert result["fields"]["isbn_edition_unspecified"] == OTHER
    assert result["fields"]["public_gtin13_edition_unspecified"] == EAN
    assert "isbn_digital" not in result["fields"]
    assert all(proof["edition_proven"] is False
               for proof in result["field_evidence"].values())


@pytest.mark.parametrize("bad", [
    "9780000000000", "", None, 9782092591093, "PRIVATE_PERSON@example.invalid",
    "https://private.invalid/?token=PRIVATE", ["9782092591093"],
])
def test_invalid_or_private_gtin13_does_not_cross_boundary(bad):
    result = detail(gtin13=bad)
    assert "public_gtin13_edition_unspecified" not in result["fields"]
    assert "PRIVATE" not in repr(result)


def test_tampered_cached_gtin13_is_refused():
    result = detail(gtin13=EAN)
    result["fields"]["public_gtin13_edition_unspecified"] = "9780000000000"
    with pytest.raises(PublicDetailError, match="public_jsonld_evidence_unverified"):
        validated_public_jsonld_fields(result, URL)


def test_owned_detail_keeps_ownership_and_unknown_purchased_format():
    s = NumilogSession(credential_id="synthetic", label="Compte",
                       credentials={}, options={}, config={}, diagnostics=None)
    try:
        row = {"lid": "42", "variant": "()", "title": "Fiche test",
               "cover": None, "public_url": URL}
        owned = s._normalize(row)
        enriched = merge_attested_owned_direct_public_detail(
            owned, detail(gtin13=EAN),
        )
        assert enriched.record_id == owned.record_id
        assert enriched.owned is True and enriched.downloadable is True
        assert enriched.download_url is None and enriched.format is None
        proof = enriched.metadata["public_detail_enrichment"]
        assert proof["public_jsonld"]["fields"]["public_gtin13_edition_unspecified"] == EAN
        assert proof["public_jsonld"]["field_evidence"][
            "public_gtin13_edition_unspecified"]["edition_proven"] is False
        assert proof["format_edition_proven"] is False
        assert "isbn_digital" not in enriched.metadata
    finally:
        s.close()
