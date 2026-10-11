"""P1 #959: pure, conservative projection of already fetched library notices."""
from types import SimpleNamespace as NS

import pytest

from youth_catalog import (
    YouthCatalogPlugin, attest_library_notices, normalize_record,
)


ISBN = "9780306406157"
OTHER_ISBN = "9782211348171"


def book(value=ISBN):
    return normalize_record(
        {"title": "Livre jeunesse", "isbn13": value}, source_id="ecole-loisirs",
    )


def result(provider="library.one", *, isbn=ISBN, attested=True,
           url="https://catalogue.example.org/record/123",
           record_id="123", **extra):
    metadata = {"isbn13": isbn, "direct_notice_attested": attested}
    metadata.update(extra.pop("metadata", {}))
    return NS(
        source_provider=provider, record_id=record_id, friendly_url=url,
        metadata=metadata, **extra,
    )


def test_two_libraries_same_edition_have_independent_attested_catalogue_notices():
    matches = attest_library_notices(book(), [
        result("library.one", record_id="x1",
               url="https://catalogue-a.example.org/notice/x1",
               owned=True, downloadable=True),
        result("library.two", record_id="x2",
               url="https://catalogue-b.example.org/notices/x2",
               status="available", source_credential="someone-else"),
    ])
    assert len(matches) == 2
    assert [m.source_provider for m in matches] == ["library.one", "library.two"]
    assert [m.record_id for m in matches] == ["x1", "x2"]
    assert all(m.isbn13 == ISBN for m in matches)
    assert all(m.status == "catalogue_reference" for m in matches)
    assert all(m.freshness == "unknown" for m in matches)
    assert all(not hasattr(m, "source_credential") for m in matches)
    assert all(not hasattr(m, "available") for m in matches)


def test_no_direct_notice_without_strong_isbn_and_explicit_attestation():
    candidates = [
        result(isbn=OTHER_ISBN),
        result(isbn="3127020507597"),  # Valid EAN != ISBN
        result(isbn=""),
        result(attested=False),
        result(attested="true"),
        result(metadata={"direct_notice_attested": 1}),
    ]
    assert attest_library_notices(book(), candidates) == ()
    assert attest_library_notices(book("bad ISBN"), [result()]) == ()


@pytest.mark.parametrize("url", [
    "http://catalogue.example.org/record/123",
    "https://catalogue.example.org/record/123?session=private",
    "https://catalogue.example.org/record/123#auth=private",
    "https://user:password@catalogue.example.org/record/123",
    "https://catalogue.example.org/",
    "https://127.0.0.1/record/123",
    "https://10.0.0.1/record/123",
    "https://localhost/record/123",
    "https://catalogue.local/record/123",
    "https://catalogue.example.org:8080/record/123",
    "https://catalogue.example.org/record/123\nsecret",
    "https://catalogue.example.org/record/123\tprivate",
])
def test_direct_notice_url_must_be_public_https_without_query_or_secrets(url):
    assert attest_library_notices(book(), [result(url=url)]) == ()


def test_invalid_provenance_or_record_ids_do_not_reach_ui():
    candidates = [
        result(provider="https://fake.invalid"),
        result(provider=""),
        result(provider="PRIVATE\nINJECTED"),
        result(record_id="../token"),
        result(record_id=""),
        result(record_id="https://evil.invalid/secret"),
    ]
    assert attest_library_notices(book(), candidates) == ()


def test_matching_isbn10_is_canonicalized_and_rows_are_deduplicated_by_provider_record():
    matches = attest_library_notices(book(), [
        result("library.one", isbn="0306406152"),
        result("library.one", isbn=ISBN, url="https://catalogue.example.org/record/duplicate"),
        result("library.two", isbn=ISBN),
    ])
    assert len(matches) == 2
    assert {m.source_provider for m in matches} == {"library.one", "library.two"}


def test_bounded_stateless_projection_no_implicit_search_or_cache():
    plugin = YouthCatalogPlugin()
    assert plugin.attested_library_notices(book(), [result()]) == attest_library_notices(
        book(), [result()],
    )
    rows = [result("library.one", record_id=str(i),
                   url=f"https://catalogue.example.org/record/{i}")
            for i in range(120)]
    assert len(attest_library_notices(book(), rows, limit=5)) == 5
    assert len(attest_library_notices(book(), rows, limit=100)) == 100
    assert attest_library_notices(book(), rows, limit=0) == ()
    # No write into the per-identity Youth cache, no call to a provider.
    assert plugin.index.entries("alice") == ()
    assert plugin.index.entries("bob") == ()


def test_bad_provider_metadata_is_not_treated_as_library_evidence():
    assert attest_library_notices(book(), [
        result(metadata={"direct_notice_attested": "yes"}),
        result(metadata={"isbn13": "invalid"}),
        NS(source_provider="library.one", record_id="x", friendly_url="https://catalogue.example.org/record/x", metadata=None),
    ]) == ()
