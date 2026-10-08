from youth_discovery import (
    canonical_url,
    detect_age_evidence,
    discover_sources,
    html_visible_text,
    is_public_candidate_url,
)


def test_detects_strong_age_labels_fr_and_en():
    evidence = detect_age_evidence("Interest age: 8-10. Reading age 7 to 9. À partir de 6 ans.")
    assert [(e.kind, e.age_min, e.age_max) for e in evidence] == [
        ("interest", 8, 10),
        ("reading", 7, 9),
        ("recommended", 6, None),
    ]


def test_generic_age_requires_youth_context():
    assert detect_age_evidence("Alice is 12 years old and lives in Paris") == ()
    evidence = detect_age_evidence("A children's book for readers 12 years old")
    assert evidence and evidence[0].strength == 55


def test_rejects_adult_age_values():
    assert detect_age_evidence("For ages 18 to 25") == ()
    assert detect_age_evidence("Reading age: 21+") == ()


def test_html_visible_text_ignores_scripts_and_keeps_meta_description():
    html = '<html><head><meta name="description" content="For ages 8 to 10"><script>Reading age: 4</script></head><body>Book</body></html>'
    text = html_visible_text(html)
    assert "For ages 8 to 10" in text
    assert "Reading age: 4" not in text


def test_canonical_url_strips_fragment_and_credentials_rejected():
    assert canonical_url("HTTPS://Example.org/path#x") == "https://example.org/path"
    assert canonical_url("https://u:p@example.org/x") == ""


def test_discovery_groups_by_domain_and_scores_explicit_evidence():
    calls = []

    def search(query, limit=20):
        calls.append(query)
        return [
            {"url": "https://kids.example/books/one", "title": "One", "snippet": "Interest age: 8-10"},
            {"url": "https://kids.example/books/two", "title": "Two", "snippet": "Reading age: 7-9"},
            {"url": "https://noise.example/about", "title": "About", "snippet": "Our editor is 42 years old"},
        ]

    report = discover_sources("space books", search=search)
    assert len(calls) == 3
    assert [c.domain for c in report.candidates] == ["kids.example"]
    candidate = report.candidates[0]
    assert candidate.status == "strong"
    assert {e.kind for e in candidate.evidence} == {"interest", "reading"}
    assert len(candidate.sample_urls) == 2


def test_page_inspection_can_qualify_candidate_when_snippet_has_no_age():
    def search(query, limit=20):
        return [{"url": "https://catalog.example/book/a", "title": "Book A", "snippet": "A new children's title"}]

    fetched = []

    def fetch(url):
        fetched.append(url)
        return {"content_type": "text/html", "text": "<p>For ages 9 to 12</p>"}

    report = discover_sources("Book A", search=search, fetch=fetch, inspect_limit=1)
    assert report.inspected_pages == 1
    assert len(fetched) == 1
    assert report.candidates[0].domain == "catalog.example"
    assert report.candidates[0].evidence[0].origin == "page"


def test_inspection_is_bounded_and_never_follows_links():
    def search(query, limit=20):
        return [
            {"url": f"https://s{i}.example/book", "title": f"Book {i}", "snippet": "children's book"}
            for i in range(8)
        ]

    calls = []

    def fetch(url):
        calls.append(url)
        return '<a href="https://other.example/hidden">For ages 5 to 7</a>'

    report = discover_sources("books", search=search, fetch=fetch, inspect_limit=3)
    assert len(calls) == 3
    assert report.inspected_pages == 3


def test_fetch_failure_is_diagnostic_and_isolated():
    def search(query, limit=20):
        return [
            {"url": "https://good.example/a", "snippet": "For ages 6 to 8"},
            {"url": "https://bad.example/b", "snippet": "children's book"},
        ]

    def fetch(url):
        if "bad.example" in url:
            raise RuntimeError("offline")
        return "For ages 6 to 8"

    report = discover_sources("books", search=search, fetch=fetch, inspect_limit=2)
    assert [c.domain for c in report.candidates] == ["good.example"]
    assert len(report.fetch_errors) == 1
    assert "offline" in report.fetch_errors[0]


def test_search_failure_is_isolated_between_query_variants():
    n = 0

    def search(query, limit=20):
        nonlocal n
        n += 1
        if n == 2:
            raise RuntimeError("metasearch down")
        return [{"url": "https://kids.example/a", "snippet": "For ages 8 to 10"}]

    report = discover_sources("books", search=search)
    assert len(report.candidates) == 1
    assert len(report.search_errors) == 1


def test_no_activation_or_registration_side_effect_exists():
    state = {"activated": False}

    def search(query, limit=20):
        return [{"url": "https://kids.example/a", "snippet": "For ages 8 to 10"}]

    report = discover_sources("books", search=search)
    assert report.candidates
    assert state["activated"] is False


def test_private_and_local_urls_are_never_candidates():
    assert not is_public_candidate_url("http://127.0.0.1/admin")
    assert not is_public_candidate_url("http://10.1.2.3/catalog")
    assert not is_public_candidate_url("http://[::1]/catalog")
    assert not is_public_candidate_url("https://books.local/catalog")
    assert is_public_candidate_url("https://books.example/catalog")

    def search(query, limit=20):
        return [
            {"url": "http://127.0.0.1/secret", "snippet": "For ages 8 to 10"},
            {"url": "https://books.example/a", "snippet": "For ages 8 to 10"},
        ]

    report = discover_sources("books", search=search)
    assert [candidate.domain for candidate in report.candidates] == ["books.example"]
