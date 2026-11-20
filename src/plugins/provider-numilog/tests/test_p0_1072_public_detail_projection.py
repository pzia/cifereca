"""Exact anonymous source declaration from actual Numilog product JSON-LD."""
import json
from copy import deepcopy

import pytest

from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public_detail import (
    parse_public_detail_jsonld, purchase_public_detail_response,
)
from cifereca_numilog_public_provider import NumilogPublicSession
from tests.test_public_detail_transport_p0 import URL


def detail(**fields):
    data = {"@type": "Book", "url": URL, "name": "Titre public",
            "author": [{"name": "Auteur public"}],
            "publisher": {"name": "Éditeur public"},
            "description": "Résumé public", "datePublished": "2025-03-15",
            "isbn": "9782702194287", **fields}
    return parse_public_detail_jsonld(
        '<script type="application/ld+json">' + json.dumps(data) + '</script>',
        URL,
    )


def test_qualified_product_enters_public_c_abi_without_private_coordinates():
    raw = detail()
    raw["private_account"] = "secret account marker"
    response = purchase_public_detail_response(raw, product_url=URL)
    row, = response.results
    claim, = response.public_notices
    assert claim.result is row
    assert row.source_provider == "numilog-catalog"
    assert row.source_credential == "__public__"
    assert row.record_id == "numilog:public:" + URL.split("/")[3]
    assert row.source_account_id is None and row.format is None
    assert not row.owned and not row.downloadable and row.download_url is None
    assert row.thumbnail_url is None and row.friendly_url == URL
    session = NumilogPublicSession(
        credential_id="__public__", label="", credentials={}, options={},
    )
    session.connect()
    try:
        projected, = _public_rows_from_response(response, session)
        observation = admit_public_search_catalogue(
            projected, identity_id="alice", observed_at=100, public_cover_url=None,
        )
        assert observation is not None and observation.parser_version == "2"
        assert observation.key.visibility == "public"
        assert observation.key.identity_id is None
        fields = observation.fields_copy()["search_result"]
        assert observation.status == "partial" and fields["thumbnail_url"] is None
        bibliography = fields["public_bibliography"]
        assert bibliography["title"] == "Titre public"
        assert bibliography["author"] == "Auteur public"
        assert bibliography["publisher"] == "Éditeur public"
        assert bibliography["description"] == "Résumé public"
        assert bibliography["published_at"] == "2025-03-15"
        assert bibliography["format"] is None
        assert fields["public_web_references"]["links"] == [
            {"url": URL, "evidence": "numilog-catalog.public_product_jsonld"},
        ]
        assert "secret account marker" not in observation.raw_json
        assert "9782702194287" not in observation.raw_json
    finally:
        session.close()


@pytest.mark.parametrize("mutation", ["owned", "url", "proof", "missing_title"])
def test_conflicting_product_declaration_refused(mutation):
    raw = deepcopy(detail())
    if mutation == "owned":
        raw["owned"] = True
    elif mutation == "url":
        raw["product_url"] = "https://www.numilog.com/123/other.ebook"
    elif mutation == "proof":
        raw["field_evidence"]["title"]["edition_proven"] = True
    else:
        raw["fields"].pop("title")
        raw["field_evidence"].pop("title")
    with pytest.raises(ProviderCapabilityError, match="purchase_detail_evidence_unverified"):
        purchase_public_detail_response(raw, product_url=URL)


def test_oversize_values_omitted_not_truncated():
    raw = detail(description="a" * 4001,
                 author=[{"name": "a" * 200}, {"name": "b" * 200}, {"name": "c" * 200}])
    row, = purchase_public_detail_response(raw, product_url=URL).results
    assert row.author is None and row.description is None
    assert row.title == "Titre public"


@pytest.mark.parametrize("series,expected", [
    ({"@type": "BookSeries", "name": "Cycle public"}, "Cycle public"),
    ({"@type": "CreativeWorkSeries", "name": "Cycle public"}, "Cycle public"),
    ({"@type": "Book", "name": "Autre livre"}, None),
    ({"name": "Sans type"}, None),
    ({"@type": "BookSeries", "name": "x" * 241}, None),
])
def test_exact_product_series_is_a_structured_public_relation(series, expected):
    response = purchase_public_detail_response(detail(isPartOf=series), product_url=URL)
    row, = response.results
    session = NumilogPublicSession(credential_id="__public__", label="", credentials={}, options={})
    session.connect()
    try:
        projected, = _public_rows_from_response(response, session)
        observation = admit_public_search_catalogue(
            projected, identity_id="alice", observed_at=100, public_cover_url=None)
        assert observation is not None
        relation = observation.fields_copy()["search_result"]["public_bibliography"].get("series_relation")
        if expected is None:
            assert relation is None and "series" not in row.metadata
        else:
            assert relation == {
                "label": expected, "source": "numilog-catalog.public_product_jsonld",
                "source_field": "isPartOf.name", "scope": "NUMILOG_PUBLIC",
                "confidence": "explicit_record_field", "freshness": "search_response",
            }
        assert row.format is None and not row.owned and not row.downloadable
    finally:
        session.close()
