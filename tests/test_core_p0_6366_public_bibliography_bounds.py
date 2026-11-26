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


@pytest.mark.parametrize("mutation", [
    {"source": "foreign.search"},
    {"scope": "other-scenario"},
    {"confidence": "page_facet"},
    {"freshness": "cache"},
    {"source_field": "https://private.example/"},
    {"token": "secret"},
])
def test_public_series_projection_does_not_promote_malformed_proof(mutation):
    proof = {
        "source": "synthetic-a.search", "source_field": "Search.RecordLink",
        "scope": "search", "confidence": "linked_search_relation",
        "freshness": "search_response", **mutation,
    }
    actual = project({"series": ["Cycle A"], "bibliography_evidence": {"series": proof}})
    assert actual.metadata == {"catalogue_access": "shared_read_only", "series": ["Cycle A"]}


def test_public_series_projection_keeps_only_exact_bounded_proof():
    proof = {
        "source": "synthetic-a.search", "source_field": "Search.RecordLink",
        "scope": "search", "confidence": "linked_search_relation",
        "freshness": "search_response",
    }
    actual = project({"series": ["Cycle A"], "bibliography_evidence": {
        "series": proof, "account": {"secret": "not public"},
    }})
    assert actual.metadata["bibliography_evidence"] == {"series": proof}
    assert actual.metadata["bibliography_evidence"]["series"] is not proof
    assert actual.metadata["series"] == ["Cycle A"]


def test_structured_public_series_proof_keeps_its_own_kind():
    proof = {
        "source": "synthetic-a.product", "source_field": "isPartOf.name",
        "scope": "search", "confidence": "explicit_record_field",
        "freshness": "search_response",
    }
    actual = project({"series": ["Cycle A"], "bibliography_evidence": {"series": proof}})
    assert actual.metadata["bibliography_evidence"] == {"series": proof}
    assert actual.metadata["series"] == ["Cycle A"]



class _CollidingForeignKey:
    def __init__(self, native_name):
        self.native_name = native_name

    def __hash__(self):
        return hash(self.native_name)

    def __eq__(self, other):
        raise AssertionError("foreign provider-key equality executed")


def test_public_bibliography_rejects_foreign_hash_collisions_without_lookup():
    from cifereca.core.public_search_projection import (
        declared_public_classification, declared_public_series_relation,
        declared_public_language_observation,
    )

    # The key collides with the exact field which older code queried with
    # metadata.get("subjects"). Its __eq__ must never execute on admission.
    hostile = {_CollidingForeignKey("subjects"): "Roman"}
    assert _public_bibliographic_metadata(hostile) is None
    assert project(hostile) is None
    assert declared_public_classification(hostile) is None
    assert declared_public_series_relation(
        hostile, provider_id="synthetic-a", scenario="search",
    ) is None
    assert declared_public_language_observation(
        hostile, provider_id="synthetic-a", scenario="search",
    ) is None


@pytest.mark.parametrize("field", ("classification", "series", "language"))
def test_structured_public_proof_rejects_foreign_keys_before_set_or_get(field):
    from cifereca.core.public_search_projection import (
        decode_public_classification, decode_public_series_relation,
        decode_public_language_observation,
        declared_public_series_relation, declared_public_language_observation,
    )
    if field == "classification":
        assert decode_public_classification({
            _CollidingForeignKey("subjects"): ["Roman"],
        }) is None
        return
    if field == "series":
        injected = {_CollidingForeignKey("source"): "synthetic-a.search"}
        assert decode_public_series_relation(
            injected, provider_id="synthetic-a", scenario="search",
        ) is None
        assert declared_public_series_relation(
            {"series": ["Cycle A"], "bibliography_evidence": {"series": injected}},
            provider_id="synthetic-a", scenario="search",
        ) is None
    else:
        injected = {_CollidingForeignKey("raw_tags"): ["fr"]}
        assert decode_public_language_observation(
            injected, provider_id="synthetic-a", scenario="search",
        ) is None
        assert declared_public_language_observation(
            {"languages": ["fr"], "bibliography_evidence": {"languages": injected}},
            provider_id="synthetic-a", scenario="search",
        ) is None


@pytest.mark.parametrize("field", ("isbn", "isbns"))
@pytest.mark.parametrize("invalid", (
    "9780306406158", "4006381333931", "0306406153", "",
    ["9780306406157", "9780306406158"], [], "9" * 65,
))
def test_public_projection_omits_invalid_isbn_field_but_keeps_other_facts(field, invalid):
    actual = project({field: invalid, "subjects": ["Roman"]})
    assert actual is not None
    assert actual.metadata == {
        "catalogue_access": "shared_read_only", "subjects": ["Roman"],
    }


def test_public_projection_preserves_literal_valid_isbns_without_edition_inference():
    values = ["0-306-40615-2", "9780306406157", "9791090636071"]
    actual = project({"isbns": values, "source_record_id": "9780306406157"})
    assert actual.metadata == {
        "catalogue_access": "shared_read_only", "isbns": values,
    }
    assert actual.metadata["isbns"] is not values
    assert project({"isbn": "https://private.invalid/?token=secret"}) is None
