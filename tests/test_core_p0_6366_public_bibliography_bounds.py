"""#1023: bound already declared public bibliographic fields before C admission."""
import json

import pytest

from cifereca.core.public_search_projection import (
    _public_bibliographic_metadata, _MAX_BIBLIOGRAPHY_BYTES,
    project_public_search_result, project_public_search_results,
)
from cifereca.plugin_api import PublicSearchNotice, ProviderSearchResponse
from tests.test_public_search_projection import row


def project(metadata):
    item = row(metadata=metadata)
    response = ProviderSearchResponse([item], public_notices=(PublicSearchNotice(item),))
    direct = project_public_search_result(
        response, item, provider_id=item.source_provider,
        credential_id=item.source_credential,
    )
    batch = project_public_search_results(response)
    assert batch is not None and batch.get(id(item)) == direct
    return direct


def test_whitelist_retains_only_declared_descriptive_fields_and_copies():
    fields = {"authors": ["A", "B"], "genres": ["Roman"], "series": "Cycle A",
              "subjects": ("Histoire", "France"), "isbn": "9780306406157"}
    class Hostile:
        def __str__(self):
            raise AssertionError("unselected metadata traversed")
    incoming = {**fields, "account": Hostile(), "rights": Hostile(),
                "youth_age_ranges": Hostile(), "bibliography_evidence": Hostile()}
    actual = project(incoming)
    assert actual is not None
    expected = {**fields, "subjects": ["Histoire", "France"]}
    assert actual.metadata == {"catalogue_access": "shared_read_only", **expected}
    actual.metadata["authors"].append("C")
    assert fields["authors"] == ["A", "B"]
    assert _public_bibliographic_metadata({}) == {}


@pytest.mark.parametrize("location", (
    "/home/reader/private.epub", "//outside.invalid/path", r"C:\\private\\book.epub",
    "~/private/book.epub", "file:private.epub", "  \\\\server\\private",
))
def test_local_or_protocol_relative_location_not_public_bibliography(location):
    assert project({"series": location}) is None


def test_global_budget_counts_utf8_and_json_escaping_without_truncation():
    for fragment in ("é" * 500, '"' * 500):
        field = [fragment] * 20
        assert project({"subjects": field}) is not None
        assert project({"subjects": field, "tags": field}) is None
    one = {"subjects": ["fiction"]}
    assert len(json.dumps(_public_bibliographic_metadata(one), ensure_ascii=False,
                          separators=(",", ":")).encode()) <= _MAX_BIBLIOGRAPHY_BYTES


def test_invalid_field_is_not_silently_replaced_by_empty_inventory():
    for fields in ({"subjects":[None]}, {"genres":{"name":"fiction"}},
                   {"series":"token=secret"}, {"authors":[{}]}):
        assert _public_bibliographic_metadata(fields) is None
        assert project(fields) is None


@pytest.mark.parametrize("field", ("title", "author", "publisher", "published_at", "description"))
@pytest.mark.parametrize("location", (
    r"C:\private\book.epub", "D:/private/book.epub", "  ~\\private\\book.epub",
    "/home/reader/private.epub", "file:private.epub", "//outside.invalid/path",
))
def test_scalar_bibliography_uses_same_location_policy(field, location):
    item = row(**{field: location})
    response = ProviderSearchResponse([item], public_notices=(PublicSearchNotice(item),))
    assert project_public_search_result(
        response, item, provider_id=item.source_provider,
        credential_id=item.source_credential,
    ) is None
    batch = project_public_search_results(response)
    assert batch is not None and id(item) not in batch
