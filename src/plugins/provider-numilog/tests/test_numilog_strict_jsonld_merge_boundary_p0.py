"""P0 #1886/#1072: no opaque JSON-LD values cross catalogue/owned merges.

Synthetic public Book JSON-LD + expurgated public search listing fixture.
No account, purchase, ACSM, cookies, live HTTP or signed vendor URL.
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
from cifereca_numilog_public_detail import (
    merge_public_detail, parse_public_detail_jsonld,
)

ROOT = Path(__file__).parent / "fixtures"
PAGE = parse_public_search(
    (ROOT / "public_search_jefferson_20261016.html").read_text(),
    json.loads((ROOT / "public_search_jefferson_20261016.expected.json").read_text())["source_url"],
)
PUBLIC = replace(PAGE.items[0], source_provider="numilog-catalog")
URL = PUBLIC.friendly_url
MARKER = "PRIVATE_LICENSE"
SECRET = "https://vendor.invalid/fulfill?token=" + MARKER


def attested_detail():
    document = {
        "@context": "https://schema.org",
        "@type": "Book", "url": URL, "name": PUBLIC.title,
        "description": "Description publique cohérente.",
        "author": [{"@type": "Person", "name": "Auteur public"}],
        "publisher": {"name": "Éditeur public"},
        "datePublished": "2026-10-08",
        "inLanguage": "fr",
        "isbn": "9782075241939",
    }
    return parse_public_detail_jsonld(
        '<script type="application/ld+json">' + json.dumps(document) + '</script>',
        URL,
    )


def owned_listing():
    session = NumilogSession(
        credential_id="alice", label="Compte", credentials={},
        options={}, config={}, diagnostics=None,
    )
    try:
        fresh = session._normalize({
            "lid": "42", "variant": "()", "title": PUBLIC.title,
            "cover": None, "public_url": URL,
        })
    finally:
        session.close()
    enriched, status = merge_attested_public_listing(fresh, PAGE.items)
    assert status == "attested_listing_match"
    return enriched


def both_merges_reject(detail):
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict") as catalog:
        merge_public_detail(PUBLIC, deepcopy(detail))
    with pytest.raises(ProviderCapabilityError, match="public_detail_evidence_conflict") as private:
        merge_attested_owned_public_detail(owned_listing(), deepcopy(detail))
    assert MARKER not in str(catalog.value)
    assert MARKER not in str(private.value)


@pytest.mark.parametrize("bad_source", [
    "PRIVATE_LICENSE", "public_product_jsonld_Anything",
    "public_product_jsonld_Book " , None, {}, 2,
])
def test_spoofed_jsonld_sources_never_cross_either_merge(bad_source):
    detail = attested_detail()
    detail["field_evidence"]["description"]["source"] = bad_source
    both_merges_reject(detail)


@pytest.mark.parametrize(("key", "bad_value"), [
    ("description", {"license": MARKER}),
    ("description", [SECRET]),
    ("description", "value=" + MARKER + "\n"),
    ("title", [MARKER]),
    ("publisher", {"private": MARKER}),
    ("language", {"private": MARKER}),
    ("published_at", "2026-02-30"),
    ("published_at", "20261008"),
    ("isbn_edition_unspecified", SECRET),
    ("authors", [{"name": MARKER}]),
    ("authors", [MARKER, None]),
    ("authors", [MARKER] * 33),
])
def test_unvalidated_optional_jsonld_fields_not_stored(key, bad_value):
    detail = attested_detail()
    detail["fields"][key] = bad_value
    both_merges_reject(detail)


@pytest.mark.parametrize(("key", "bad_value"), [
    ("kind", "observed_public_html"),
    ("edition_proven", True),
    ("source_url", SECRET),
    ("extra", {"secret": MARKER}),
])
def test_forged_proof_attributes_are_not_accepted(key, bad_value):
    detail = attested_detail()
    detail["field_evidence"]["publisher"][key] = bad_value
    both_merges_reject(detail)


def test_unrecognized_fields_not_retained_in_public_or_owned_metadata():
    detail = attested_detail()
    detail["fields"]["authorization"] = {"token": MARKER}
    detail["field_evidence"]["authorization"] = dict(detail["field_evidence"]["title"])
    both_merges_reject(detail)


def test_mutating_caller_jsonld_after_merge_does_not_change_public_projection():
    detail = attested_detail()
    # Missing author on listing makes the copied public author visible.
    result = merge_public_detail(replace(PUBLIC, author=None), detail)
    assert result.author == "Auteur public"
    detail["fields"]["authors"].append(MARKER)
    detail["field_evidence"]["authors"]["source"] = MARKER
    assert result.metadata["public_detail"]["fields"]["authors"] == ["Auteur public"]
    assert MARKER not in repr(result.metadata["public_detail"])


def test_mutating_caller_jsonld_after_owned_merge_does_not_change_private_projection():
    detail = attested_detail()
    result = merge_attested_owned_public_detail(owned_listing(), detail)
    assert result.owned and result.download_url is None
    assert result.metadata["public_detail_enrichment"]["rights_proven_by_public_detail"] is False
    assert "isbn_digital" not in result.metadata
    detail["fields"]["authors"].append(MARKER)
    detail["field_evidence"]["authors"]["source"] = MARKER
    proof = result.metadata["public_detail_enrichment"]["public_jsonld"]
    assert proof["fields"]["authors"] == ["Auteur public"]
    assert MARKER not in repr(proof)
    assert "PRIVATE_LICENSE" not in repr(result.metadata)


def test_legitimate_book_metadata_still_projects_without_new_rights():
    detail = attested_detail()
    public = merge_public_detail(PUBLIC, detail)
    assert public.owned is False and public.downloadable is False
    assert public.download_url is None
    assert public.metadata["public_detail"]["fields"]["published_at"] == "2026-10-08"
    assert public.metadata["public_detail"]["fields"]["isbn_edition_unspecified"] == "9782075241939"
    owned = merge_attested_owned_public_detail(owned_listing(), detail)
    assert owned.owned is True and owned.download_url is None
    assert owned.metadata["public_detail_enrichment"]["format_edition_proven"] is False
    assert owned.metadata["public_detail_enrichment"]["rights_proven_by_public_detail"] is False
