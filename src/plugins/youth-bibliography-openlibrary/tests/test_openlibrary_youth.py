import httpx

from openlibrary_youth import (
    BibliographyRecord,
    OpenLibraryBibliography,
    normalize_isbn,
    normalize_olid,
    parse_direct_record,
    parse_record,
)


def search_doc(**changes):
    row = {
        "key": "/works/OL45804W",
        "title": "Fantastic Mr. Fox",
        "author_name": ["Roald Dahl"],
        "first_publish_year": 1970,
        "isbn": ["9780140328721", "0140328726"],
        "language": ["eng", "fre"],
        "subject": ["Foxes", "Children's stories"],
        "publisher": ["Puffin"],
        "edition_key": ["OL7353617M"],
        "cover_i": 8739161,
    }
    row.update(changes)
    return row


def test_search_record_is_bibliographic_only_and_never_has_age():
    record = parse_record(search_doc())
    assert isinstance(record, BibliographyRecord)
    assert record.title == "Fantastic Mr. Fox"
    assert record.authors == ("Roald Dahl",)
    assert "9780140328721" in record.isbns
    assert record.age_ranges == ()
    assert record.metadata["bibliographic_only"] is True
    assert record.metadata["age_evidence"] == "none"
    assert "age" not in {str(key).casefold() for key in record.__dict__ if key != "age_ranges"}


def test_invalid_search_rows_are_ignored():
    assert parse_record(None) is None
    assert parse_record({"key": "/works/OL45804W"}) is None
    assert parse_record({"title": "Missing key"}) is None


def test_isbn_and_olid_normalization_are_strict():
    assert normalize_isbn("0-14-032872-6") == "9780140328721"
    assert normalize_isbn("9780140328721") == "9780140328721"
    assert normalize_isbn("9780000000000") == ""
    assert normalize_olid("OL45804W") == "OL45804W"
    assert normalize_olid("/books/OL7353617M") == "OL7353617M"
    assert normalize_olid("OL23919A") == ""


def test_direct_work_record_keeps_subjects_without_inventing_age():
    record = parse_direct_record({
        "title": "Fantastic Mr. Fox",
        "authors": [{"author": {"key": "/authors/OL34184A"}}],
        "subjects": ["Foxes", "Children's stories"],
        "covers": [8739161],
        "first_publish_date": "1970",
    }, olid="OL45804W")
    assert record is not None
    assert record.record_id == "OL45804W"
    assert record.authors == ("OL34184A",)
    assert record.subjects == ("Foxes", "Children's stories")
    assert record.first_publish_year == 1970
    assert record.age_ranges == ()
    assert record.metadata["age_evidence"] == "none"


def test_direct_edition_record_normalizes_isbn_language_and_publisher():
    record = parse_direct_record({
        "title": "Fantastic Mr. Fox",
        "isbn_13": ["9780140328721"],
        "isbn_10": ["0140328726"],
        "languages": [{"key": "/languages/eng"}],
        "publishers": ["Puffin"],
        "publish_date": "October 1, 1988",
    }, olid="OL7353617M")
    assert record is not None
    assert record.isbns == ("9780140328721",)
    assert record.languages == ("eng",)
    assert record.publishers == ("Puffin",)
    assert record.edition_ids == ("OL7353617M",)
    assert record.first_publish_year == 1988


def test_search_is_bounded_and_uses_selected_fields():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": [search_doc()] * 30},
        )

    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    rows = source.search("fox", limit=500, page=999, language="EN")
    assert len(rows) == 20
    request = seen[0]
    assert request.url.params["q"] == "fox"
    assert request.url.params["limit"] == "20"
    assert request.url.params["page"] == "100"
    assert request.url.params["lang"] == "en"
    assert request.url.params["fields"]
    assert "*" not in request.url.params["fields"]


def test_lookup_isbn_filters_non_matching_search_rows():
    good = search_doc(isbn=["9780140328721"])
    bad = search_doc(key="/works/OL1W", title="Other", isbn=["9780306406157"])

    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"docs": [bad, good]})
        )),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    rows = source.lookup_isbn("0140328726")
    assert [row.title for row in rows] == ["Fantastic Mr. Fox"]
    assert source.lookup_isbn("not-an-isbn") == ()


def test_lookup_olid_uses_documented_json_object_endpoint_and_cache():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "title": "Fantastic Mr. Fox",
                "authors": [{"author": {"key": "/authors/OL34184A"}}],
                "subjects": ["Foxes"],
            },
        )

    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    first = source.lookup_olid("OL45804W")
    second = source.lookup_olid("/works/OL45804W")
    assert first == second
    assert len(calls) == 1
    assert calls[0].endswith("/works/OL45804W.json")
    assert first[0].notice_url == "https://openlibrary.org/works/OL45804W"


def test_lookup_edition_olid_uses_books_endpoint():
    seen = []

    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: seen.append(str(request.url)) or httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"title": "Edition", "isbn_13": ["9780140328721"]},
            )
        )),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    rows = source.lookup_olid("OL7353617M")
    assert rows[0].edition_ids == ("OL7353617M",)
    assert seen[0].endswith("/books/OL7353617M.json")


def test_404_olid_is_clean_empty_result():
    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(404, headers={"content-type": "application/json"}, json={})
        )),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    assert source.lookup_olid("OL45804W") == ()


def test_non_json_oversize_and_invalid_json_fail_explicitly():
    non_json = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>")
        )),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    try:
        non_json.search("fox")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "not JSON" in str(exc)

    oversized = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * 2_000_001)
        )),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    try:
        oversized.search("fox")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "2 MB" in str(exc)


def test_cache_prevents_duplicate_search_requests():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"docs": [search_doc()]})

    source = OpenLibraryBibliography(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 100.0,
        sleeper=lambda seconds: None,
    )
    assert source.search("fox") == source.search("fox")
    assert calls == 1
