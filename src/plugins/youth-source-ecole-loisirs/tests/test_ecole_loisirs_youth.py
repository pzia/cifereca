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


def test_next_cursor_requires_explicit_more_results_link():
    assert next_cursor(HTML, current_page=1) == "2"
    assert next_cursor("<a href='?page=2'>Afficher plus de résultats</a>", current_page=1) == "2"
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
