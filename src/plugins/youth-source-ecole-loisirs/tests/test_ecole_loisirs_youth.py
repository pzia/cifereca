import httpx
import pytest

from ecole_loisirs_youth import (
    EcoleLoisirsYouthSource,
    SEARCH,
    next_cursor,
    normalize_isbn13,
    parse_age_label,
    parse_listing,
    search_url,
)


HTML = """
<html><body>
  <div>3 à 6 ans</div>
  <a href="/livre/histoire-petit-loup">L'histoire de Petit Loup</a>
  <div>Sylvie Neeman</div>
  <div>album</div>
  <div>- Collection Loulou & cie</div>
  <p>Une histoire...</p>
  <div>ISBN: 9782211348171</div>
  <a href="/livre/histoire-petit-loup">Lire la suite</a>

  <div>13 ans et +</div>
  <a href="/livre/roman-ado">Roman ado</a>
  <div>Alice Auteur, Bob Auteur</div>
  <div>roman</div>
  <div>ISBN: 9782211345613</div>

  <div>indéterminé</div>
  <a href="/livre/sans-age">Sans âge</a>
  <div>album</div>
  <div>ISBN: 9782211324649</div>

  <a href="/recherche-avancee?page=2&search_api_views_fulltext=loup&types=livre">Afficher plus de résultats</a>
</body></html>
"""


def test_parses_supported_age_labels():
    a = parse_age_label("0 à 3 ans")
    assert (a.age_min, a.age_max, a.qualified) == (0, 3, True)
    b = parse_age_label("à partir de 7 ans")
    assert (b.age_min, b.age_max) == (7, None)
    c = parse_age_label("13 ans et +")
    assert (c.age_min, c.age_max) == (13, None)
    d = parse_age_label("indéterminé")
    assert (d.age_min, d.age_max, d.qualified) == (None, None, False)
    assert parse_age_label("18 ans et +") is None


def test_listing_preserves_explicit_age_isbn_authors_and_type():
    rows = parse_listing(HTML)
    assert [row["title"] for row in rows] == [
        "L'histoire de Petit Loup",
        "Roman ado",
        "Sans âge",
    ]
    first = rows[0]
    assert first["isbn13"] == "9782211348171"
    assert first["authors"] == ("Sylvie Neeman",)
    assert first["book_type"] == "album"
    assert first["age_ranges"][0]["age_min"] == 3
    assert first["age_ranges"][0]["age_max"] == 6
    assert first["age_ranges"][0]["age_kind"] == "publisher"


def test_unknown_age_stays_unqualified_instead_of_being_inferred():
    row = parse_listing(HTML)[2]
    assert row["age_ranges"] == []
    assert row["metadata"]["age_label"] == "indéterminé"
    assert row["metadata"]["age_evidence"] == "ecole-loisirs.search.unknown"


def test_open_ended_age_is_preserved():
    row = parse_listing(HTML)[1]
    assert row["age_ranges"][0]["age_min"] == 13
    assert row["age_ranges"][0]["age_max"] is None
    assert row["authors"] == ("Alice Auteur", "Bob Auteur")


def test_bad_or_missing_isbn_is_not_published():
    html = """
      <div>3 à 6 ans</div>
      <a href="/livre/no-isbn">No ISBN</a><div>album</div>
      <div>6 à 8 ans</div>
      <a href="/livre/bad-isbn">Bad ISBN</a><div>ISBN: 9780000000000</div>
    """
    assert parse_listing(html) == ()


def test_product_link_must_stay_on_publisher_host():
    html = """
      <div>3 à 6 ans</div>
      <a href="https://evil.example/livre/test">External</a>
      <div>ISBN: 9782211348171</div>
    """
    assert parse_listing(html) == ()


def test_next_cursor_requires_explicit_immediate_next_page_link():
    assert next_cursor(HTML, current_page=1) == "2"
    assert next_cursor("<a href='?page=2'>Afficher plus de résultats</a>", current_page=1) == "2"
    assert next_cursor("<a href='?page=3'>Afficher plus de résultats</a>", current_page=1) == ""
    assert next_cursor("<a href='?page=1'>Afficher plus de résultats</a>", current_page=1) == ""
    assert next_cursor("<p>Fin</p>", current_page=1) == ""


def test_search_url_is_bounded_and_uses_book_filter():
    url = search_url("loup & école", page=500)
    assert url.startswith(SEARCH + "?")
    assert "page=100" in url
    assert "types=livre" in url
    assert "search_api_views_fulltext=loup+%26+%C3%A9cole" in url


def test_source_search_uses_public_advanced_search_and_real_cursor():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=HTML,
        )

    source = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = source.search("loup", cursor="1", limit=40)
    assert len(result["items"]) == 3
    assert result["next_cursor"] == "2"
    assert result["fresh_for_seconds"] == 86400
    assert seen[0].url.path == "/recherche-avancee"
    assert seen[0].url.params["types"] == "livre"
    assert seen[0].url.params["search_api_views_fulltext"] == "loup"


def test_invalid_cursor_fails_before_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    source = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        source.search("loup", cursor="x")
    assert calls == 0


def test_http_mime_and_size_fail_explicitly():
    failed = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(503, headers={"content-type": "text/html"}, text="down")
        ))
    )
    with pytest.raises(RuntimeError, match="503"):
        failed.search("loup")

    non_html = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={})
        ))
    )
    with pytest.raises(RuntimeError, match="not HTML"):
        non_html.search("loup")

    oversized = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * 2_000_001,
            )
        ))
    )
    with pytest.raises(RuntimeError, match="2 MB"):
        oversized.search("loup")


def test_source_info_and_isbn_validation():
    source = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="")
        ))
    )
    assert source.source_info() == {
        "source_id": "ecole-loisirs",
        "display_name": "l'école des loisirs",
        "homepage_url": SEARCH,
    }
    assert normalize_isbn13("978-2-211-34817-1") == "9782211348171"
    assert normalize_isbn13("9780000000000") == ""



def test_age_never_bleeds_from_previous_product_card():
    html = """
      <div>3 à 6 ans</div>
      <a href="/livre/aged">Avec âge</a>
      <div>album</div>
      <div>ISBN: 9782211348171</div>

      <a href="/livre/no-age">Sans âge publié</a>
      <div>roman</div>
      <div>ISBN: 9782211345613</div>
    """
    rows = parse_listing(html)
    assert [row["title"] for row in rows] == ["Avec âge"]


def test_query_is_normalized_and_bounded_to_300_characters():
    raw = "  loup   " + ("x" * 500)
    url = search_url(raw, page=1)
    request = httpx.Request("GET", url)
    value = request.url.params["search_api_views_fulltext"]
    assert len(value) == 300
    assert value.startswith("loup ")


def test_redirect_response_is_rejected_not_followed():
    source = EcoleLoisirsYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={
                    "content-type": "text/html",
                    "location": "https://evil.example/",
                },
                text="redirect",
            )
        ))
    )
    with pytest.raises(RuntimeError, match="302"):
        source.search("loup")


def test_script_style_and_svg_cannot_inject_age_or_isbn():
    html = """
      <script>3 à 6 ans</script>
      <style>.x:after { content: "ISBN: 9782211348171"; }</style>
      <svg><text>3 à 6 ans</text></svg>
      <a href="/livre/clean">Clean</a>
      <div>album</div>
      <div>ISBN: 9782211348171</div>
    """
    assert parse_listing(html) == ()


def test_ecole_diagnostic_success_keeps_published_and_unknown_ages_separate():
    import json
    from types import SimpleNamespace

    sent, requests = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.diagnostic_snapshot() == {}
    source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: sent.append((args, kwargs))
    ))
    result = source.search("loup", limit=4)
    assert len(result["items"]) == 3
    d = source.diagnostic_snapshot()
    assert d["source"] == "ecole-loisirs"
    assert d["state"] == "items" and d["phase"] == "complete"
    assert d["http_status"] == 200 and d["request_count"] == 1
    assert d["content_type_is_html"] is True
    assert d["html_bytes"] == len(HTML.encode("utf-8"))
    assert len(d["html_sha256_prefix"]) == 16
    assert d["anchor_count"] == 5
    assert d["product_url_count"] == 3
    assert d["returned_items"] == 3
    assert d["qualified_age_count"] == 2
    assert d["unqualified_age_count"] == 1
    assert d["text_filter_present"] is True and d["next_cursor_present"] is True
    assert d["duration_ms"] >= 0 and "T" in d["attempted_at_utc"]
    assert sent[-1][0] == ("Ecole des loisirs youth catalogue collection", "ok")
    assert sent[-1][1]["detail"] == d
    assert len(requests) == 1
    encoded = json.dumps(d)
    assert "Petit Loup" not in encoded
    assert "recherche-avancee" not in encoded
    assert "9782211348171" not in encoded


def test_ecole_diagnostic_rejects_http_error_and_timeout_without_raw_details():
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(
            503, headers={"content-type": "text/html"}, text="private details"
        )
    )))
    with pytest.raises(RuntimeError, match="503"):
        source.search("private search")
    d = source.diagnostic_snapshot()
    assert d["http_status"] == 503 and d["request_count"] == 1
    assert d["phase"] == "listing" and d["state"] == "error"
    assert d["error_type"] == "RuntimeError"
    assert "private search" not in str(d)
    assert "private details" not in str(d)

    def timeout(req):
        raise httpx.ReadTimeout("secret url", request=req)

    source.client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(httpx.ReadTimeout):
        source.search("private search")
    d = source.diagnostic_snapshot()
    assert d["error_type"] == "ReadTimeout"
    assert "http_status" not in d
    assert d["request_count"] == 1
    assert "secret url" not in str(d)


def test_ecole_empty_diagnostic_is_not_claimed_as_public_health():
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>no book</p>"
        )
    )))
    assert source.search("not here")["items"] == ()
    d = source.diagnostic_snapshot()
    assert d["state"] == "empty_unverified"
    assert d["product_url_count"] == 0 and d["returned_items"] == 0


def test_ecole_broker_failure_does_not_break_source_query():
    from types import SimpleNamespace

    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    )))
    source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: 1 / 0
    ))
    assert len(source.search("loup")["items"]) == 3
    assert source.diagnostic_snapshot()["state"] == "items"


def test_http_error_preserves_typed_status_for_youth_diagnostics():
    """A single mocked HTTP request; no remote publisher contacted."""
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            429, headers={"content-type": "text/html"}, text="private body",
        )
    )))
    with pytest.raises(RuntimeError, match="HTTP 429") as raised:
        source.search("chat")
    assert raised.value.http_status == 429




def test_ecole_per_collection_request_evidence_survives_http_failure():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.search("loup")["collection_request_count"] == 1
    assert len(requests) == 1
    source.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(401, headers={"content-type": "text/html"}, text="private")
    ))
    with pytest.raises(RuntimeError, match="401") as exc:
        source.search("loup")
    assert exc.value.collection_request_count == 1
    assert source._call_requests.get() is None


def test_product_gtin_with_valid_checksum_is_not_an_isbn13():
    """Publisher sometimes labels toy/barcode EAN-13 as 'ISBN'."""
    for ean in ("3575678940134", "3127020507597"):
        assert normalize_isbn13(ean) == ""
    # ISBN-13: accept 978 and 979 prefixes with valid checksum.
    assert normalize_isbn13("9782211309684") == "9782211309684"
    assert normalize_isbn13("9791234567896") == "9791234567896"
    assert normalize_isbn13("0306406152") == "9780306406157"


def test_ecole_listing_excludes_games_and_nonbook_ean_while_keeps_real_book():
    html = """
        <div>3 à 6 ans</div>
        <a href="/livre/peluche-loup">Peluche Le loup le plus beau</a>
        <div>jeu</div><p>ISBN: 3575678940134</p>
        <div>3 à 6 ans</div>
        <a href="/livre/jeu-isbn">Jeu possédant un vrai ISBN</a>
        <div>jeu</div><p>ISBN: 9782211348171</p>
        <div>3 à 6 ans</div>
        <a href="/livre/milo-loup">Milo et le loup</a>
        <div>album</div><p>ISBN: 9782211309684</p>
        <div>3 à 6 ans</div>
        <a href="/livre/jeu-loup">Le loup — Cherche et trouve</a>
        <div>jeu</div><p>ISBN: 3127020507597</p>
    """
    rows = parse_listing(html)
    assert len(rows) == 1
    assert rows[0]["title"] == "Milo et le loup"
    assert rows[0]["isbn13"] == "9782211309684"
    assert rows[0]["book_type"] == "album"
    assert rows[0]["age_ranges"][0]["source_label"] == "3 à 6 ans"


def test_ecole_paginated_changed_markup_is_invalid_and_does_not_expose_body():
    from ecole_loisirs_youth import InvalidCatalogueStructureError
    from types import SimpleNamespace

    sent, requests = [], []
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: requests.append(req) or httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<div>Changed markup</div>'
                 '<a href="?page=2">Afficher plus de résultats</a>',
        )
    )))
    source.activate(SimpleNamespace(report_diagnostic=lambda *a, **kw: sent.append((a, kw))))
    with pytest.raises(InvalidCatalogueStructureError, match="no recognizable book links") as raised:
        source.search("loup")
    assert raised.value.collection_request_count == 1
    assert len(requests) == 1
    snapshot = source.diagnostic_snapshot()
    assert snapshot["state"] == "invalid"
    assert snapshot["phase"] == "parsing"
    assert snapshot["error_type"] == "InvalidCatalogueStructureError"
    assert snapshot["http_status"] == 200
    assert "Changed markup" not in repr(snapshot)
    assert sent[-1][0][-1] == "error"

    # All results being non-books/non-qualifying remains an unverified empty.
    source.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<a href="/livre/missing-isbn">Unqualified</a>'
                 '<a href="?page=2">Afficher plus de résultats</a>',
        )
    ))
    response = source.search("loup")
    assert response["items"] == ()
    assert response["next_cursor"] == "2"
    assert source.diagnostic_snapshot()["state"] == "empty_unverified"


def test_ecole_funnel_explains_fixture_success_and_unknown_age_without_remote_fetch():
    from types import SimpleNamespace

    snapshots, requests = [], []
    def handler(req):
        requests.append(req)
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML,
        )
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    source.activate(SimpleNamespace(report_diagnostic=lambda *args, **kw: snapshots.append(kw["detail"])))
    result = source.search("loup", limit=100)
    funnel = result["collection_pipeline"]
    assert len(result["items"]) == 3
    assert result["collection_request_count"] == 1
    assert len(requests) == 1
    assert funnel["query_mode"] == "remote"
    assert funnel["effective_limit"] == 40  # caller's 100 is not 100 analyzed
    assert funnel["http_status"] == 200
    assert funnel["html_bytes"] == len(HTML.encode("utf-8"))
    assert len(funnel["html_sha256_prefix"]) == 16
    assert funnel["anchor_count"] == 5
    assert funnel["product_link_count"] == 3
    assert funnel["candidate_count"] == 3
    assert funnel["analyzed_count"] == 3
    assert funnel["isbn_valid_count"] == 3
    assert funnel["age_published_count"] == 3
    assert funnel["age_qualified_count"] == 2
    assert funnel["age_unknown_count"] == 1
    assert funnel["returned_items"] == 3
    assert funnel["rejection_reasons"] == {}
    assert funnel["truncated_by_limit"] is False
    assert funnel["http_ms"] >= 0 and funnel["parse_ms"] >= 0
    assert snapshots[-1]["returned_items"] == 3
    assert "loup" not in repr(funnel)
    assert "9782211348171" not in repr(funnel)
    assert "/livre/" not in repr(funnel)


def test_ecole_funnel_counts_20_books_with_one_get_without_claiming_live_validation():
    # A deterministic local witness for the user's observed 20 results.
    def isbn(index):
        prefix = f"9782211{index:05d}"
        check = (10 - sum(
            int(digit) * (1 if i % 2 == 0 else 3)
            for i, digit in enumerate(prefix)
        ) % 10) % 10
        return prefix + str(check)

    html = "<html>" + "".join(
        f'<div>3 à 6 ans</div><a href="/livre/test-{i}">Livre {i}</a>'
        f'<div>album</div><div>ISBN: {isbn(i)}</div>'
        for i in range(20)
    ) + "</html>"
    requests = []
    def handler(req):
        requests.append(req)
        assert req.url.path == "/recherche-avancee"
        assert req.url.params.get("search_api_views_fulltext") == "loup"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.search("loup", limit=100)
    funnel = result["collection_pipeline"]
    assert len(result["items"]) == 20
    assert len({row["isbn13"] for row in result["items"]}) == 20
    assert result["collection_request_count"] == len(requests) == 1
    assert funnel["candidate_count"] == 20
    assert funnel["analyzed_count"] == 20
    assert funnel["isbn_valid_count"] == 20
    assert funnel["age_qualified_count"] == 20
    assert funnel["returned_items"] == 20
    assert funnel["effective_limit"] == 40
    assert funnel["rejection_reasons"] == {}
    assert funnel["truncated_by_limit"] is False


def test_ecole_funnel_classifies_loss_before_qualification_and_real_empty():
    # Published but unverified bookland ISBNs must not survive as book notices.
    valid_isbn = "9782211348171"
    markup = (
        '<a href="/livre/without-age">No age</a>'
        f'<div>ISBN: {valid_isbn}</div>'
        '<div>3 à 6 ans</div><a href="/livre/without-isbn">No ISBN</a>'
        '<div>album</div>'
        '<div>3 à 6 ans</div><a href="/livre/game">Game</a>'
        '<div>jeu</div>'
        f'<div>ISBN: {valid_isbn}</div>'
        '<div>3 à 6 ans</div><a href="/livre/good">Valid</a>'
        '<div>album</div>'
        f'<div>ISBN: {valid_isbn}</div>'
        '<div>3 à 6 ans</div><a href="/livre/good">Duplicate</a>'
        '<div>album</div>'
        f'<div>ISBN: {valid_isbn}</div>'
    )
    diagnostic = {}
    rows = parse_listing(markup, diagnostic=diagnostic)
    assert len(rows) == 1
    assert diagnostic["product_link_count"] == 5
    assert diagnostic["candidate_count"] == 4
    assert diagnostic["analyzed_count"] == 4
    assert diagnostic["isbn_valid_count"] == 2
    assert diagnostic["age_qualified_count"] == 1
    assert diagnostic["rejection_reasons"] == {
        "age_unattested": 1,
        "isbn_invalid": 1,
        "unsupported_product_type": 1,
        "duplicate": 1,
    }
    empty = {}
    assert parse_listing("<main>No matching books</main>", diagnostic=empty) == ()
    assert empty["anchor_count"] == 0
    assert empty["candidate_count"] == 0
    assert empty["returned_items"] == 0
    assert empty["rejection_reasons"] == {}


def test_ecole_http_refusal_attaches_only_same_invocation_evidence():
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(403, headers={"content-type": "text/html"}, text="private query"),
    )))
    with pytest.raises(RuntimeError, match="403") as raised:
        source.search("private query", limit=100)
    evidence = raised.value.collection_pipeline
    assert raised.value.collection_request_count == 1
    assert evidence["http_status"] == 403
    assert evidence["effective_limit"] == 40
    assert evidence["http_ms"] >= 0
    assert "html_sha256_prefix" in evidence
    assert "private query" not in repr(evidence)
