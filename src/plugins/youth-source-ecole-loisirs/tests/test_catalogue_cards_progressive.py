"""Sanitized deterministic catalogue fixtures: no captured sessions or trackers."""
from ecole_loisirs_youth import parse_catalogue_cards, parse_listing


def test_cards_without_isbn_keep_age_and_source_identity():
    html = """
    <div id="products-wrapper">
      <div class="product-item">
        <a href="/livre/loup-a-ma-porte"><div class="card-title">Un loup à ma porte</div></a>
        <div class="card-author">Nadine Brun-Cosme</div>
        <div class="card-infos"><li>Medium 14+</li><li>Age : 13 ans et +</li></div>
        <div class="card-text">Une histoire de loup.</div>
      </div>
      <div class="product-item">
        <a href="/livre/mia-loups-moi"><div class="card-title">Mia, les loups et moi</div></a>
        <div class="card-author">Anaïs Sautier</div>
        <div class="card-infos"><li>Mouche 8-10</li><li>Age : 8 à 11 ans</li></div>
      </div>
    </div>
    <div id="products-thumb-wrapper">
      <div class="product-item-thumb"><a href="/livre/loup-a-ma-porte">Duplicate thumbnail</a></div>
    </div>
    """
    diagnostic = {}
    rows = parse_listing(html, diagnostic=diagnostic)
    assert len(rows) == 2
    assert rows[0]["title"] == "Un loup à ma porte"
    assert rows[0]["record_id"] == "loup-a-ma-porte"
    assert rows[0]["age_ranges"][0]["age_min"] == 13
    assert rows[0]["age_ranges"][0]["age_max"] is None
    assert rows[1]["age_ranges"][0]["age_min"] == 8
    assert rows[1]["age_ranges"][0]["age_max"] == 11
    assert all("isbn13" not in row for row in rows)
    assert diagnostic["cards_detected"] == 2
    assert diagnostic["missing_isbn"] == 2


def test_thumbnail_duplicates_and_rejected_cards_are_observed_without_exposing_urls():
    html = """
    <div id="products-wrapper">
      <div class="product-item">
        <a href="/livre/a"><span class="card-title">Alpha</span></a>
        <div class="card-infos">Age : 8 à 11 ans</div>
      </div>
      <div class="product-item">
        <a href="/livre/a"><span class="card-title">Same edition</span></a>
        <div class="card-infos">Age : 8 à 11 ans</div>
      </div>
      <div class="product-item">
        <a href="/livre/no-age"><span class="card-title">No age</span></a>
      </div>
      <div class="product-item">
        <a href="/livre/no-title"></a><div class="card-infos">Age : 8 à 11 ans</div>
      </div>
      <div class="product-item">
        <a href="https://other.example/private?token=SECRET"><span class="card-title">Not public</span></a>
        <div class="card-infos">Age : 8 à 11 ans</div>
      </div>
    </div>
    <div id="products-thumb-wrapper">
      <div class="product-item-thumb"><a href="/livre/a">Duplicate thumbnail</a></div>
      <div class="product-item-thumb"><div><a href="/livre/a">Another thumbnail of A</a></div></div>
      <div class="product-item-thumb"><a href="/livre/not-in-primary">Unrelated thumbnail</a></div>
    </div>
    """
    d = {}
    rows = parse_catalogue_cards(html, diagnostic=d)
    assert [r["record_id"] for r in rows] == ["a"]
    assert d["cards_detected"] == 5
    assert d["cards_complete"] == 1
    assert d["thumbnail_card_count"] == 3
    assert d["thumbnail_duplicate_count"] == 1  # distinct editions, not 2 thumbnails
    assert d["candidate_count"] == 3
    assert d["analyzed_count"] == 5
    assert d["rejection_reasons"] == {
        "duplicate": 1, "age_unattested": 1, "title_missing": 1,
        "unexpected_markup": 1,
    }
    assert d["truncated_by_limit"] is False
    for secret in ("SECRET", "other.example", "not-in-primary", "Alpha"):
        assert secret not in str(d)


def test_cards_bounded_collection_never_claims_all_candidates_examined():
    html = "<div id='products-wrapper'>" + "".join(
        f'<div class="product-item"><a href="/livre/notice-{i}">'
        f'<span class="card-title">Notice {i}</span></a>'
        '<div class="card-infos">Age : 8 à 11 ans</div></div>'
        for i in range(4)
    ) + "</div>"
    d = {}
    assert len(parse_catalogue_cards(html, limit=2, diagnostic=d)) == 2
    assert d["cards_detected"] == 4
    assert d["cards_complete"] == 2
    assert d["analyzed_count"] == 2
    assert d["candidate_count"] == 2
    assert d["truncated_by_limit"] is True
    assert d["rejection_reasons"] == {}


def test_cards_reject_unattested_age_and_non_public_links():
    html = """
    <div class="product-item"><a href="https://invalid.example/livre/other">
    <div class="card-title">Outside</div></a><div class="card-infos">Age : 8 à 11 ans</div></div>
    <div class="product-item"><a href="/livre/no-age">
    <div class="card-title">Unknown</div></a><div class="card-infos">Age : indéterminé</div></div>
    """
    assert parse_catalogue_cards(html) == ()


def test_cover_safety_and_explicit_catalogue_route():
    import httpx
    from ecole_loisirs_youth import EcoleLoisirsYouthSource
    seen = []
    html = """
    <div class="product-item"><a href="/livre/un-loup">
      <span class="card-title">Un loup</span></a>
      <span class="card-author">Auteur A</span>
      <ul class="card-infos"><li>Age : 13 ans et +</li></ul>
      <img src="https://example.org/cover.jpg" data-src="/images/un-loup.jpg">
      <p class="card-text">Résumé publié</p>
    </div>
    """
    def handle(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handle)))
    result = source.search_catalogue("loup")
    assert len(seen) == result["collection_request_count"] == 1
    assert seen[0].url.path == "/catalogue/romans"
    assert seen[0].url.params["search"] == "loup"
    assert result["next_cursor"] == ""
    book = result["items"][0]
    assert book["cover_url"] == "https://www.ecoledesloisirs.fr/images/un-loup.jpg"
    assert book["description"] == "Résumé publié"
    assert "isbn13" not in book


def test_catalogue_pagination_is_reported_without_auto_fetch():
    import httpx
    from ecole_loisirs_youth import EcoleLoisirsYouthSource
    calls = []
    html = (
        '<div class="product-item"><a href="/livre/loup">'
        '<span class="card-title">Loup</span></a>'
        '<div class="card-infos">Age : 8 à 11 ans</div></div>'
        '<a href="/catalogue/romans?page=1&amp;type=romans&amp;search=loup">Suivant</a>'
    )
    def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(respond)))
    first = source.search_catalogue("loup")
    assert len(first["items"]) == 1
    assert first["next_cursor"] == "1"
    assert len(calls) == 1
    source.search_catalogue("loup", page=int(first["next_cursor"]))
    assert len(calls) == 2


def test_ui_browse_calls_catalogue_not_advanced_and_keeps_age_per_card():
    import httpx
    from ecole_loisirs_youth import EcoleLoisirsYouthSource
    requests = []
    age_labels = ["6 à 8 ans"] * 9 + ["8 à 11 ans"] * 5 + ["11 à 13 ans"] * 3 + ["13 ans et +"] * 3
    cards = []
    for n, age in enumerate(age_labels):
        cards.append(
            f'<div class="product-item"><a href="/livre/loup-{n}">'
            f'<div class="card-title">Livre loup {n}</div></a>'
            f'<div class="card-author">Auteur {n}</div>'
            f'<div class="card-infos"><li>Collection {n}</li><li>Age : {age}</li></div>'
            '<img src="https://tracker.invalid/cover.jpg">'
            '</div>'
        )
    html = '<section id="products-wrapper">' + ''.join(cards) + '</section>'
    html += '<div class="product-item-thumb"><a href="/livre/loup-0">Double</a></div>'

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(text_filter="loup", age=8, themes=("animal",), limit=40)
    assert len(requests) == result["collection_request_count"] == 1
    assert requests[0].url.path == "/catalogue/romans"
    assert len(result["items"]) == 20
    assert len({row["record_id"] for row in result["items"]}) == 20
    assert [row["age_ranges"][0]["age_min"] for row in result["items"]] == [6]*9 + [8]*5 + [11]*3 + [13]*3
    assert all("isbn13" not in row for row in result["items"])
    assert all("cover_url" not in row for row in result["items"])
    assert result["items"][0]["collection"] == "Collection 0"
    assert result["collection_pipeline"]["cards_detected"] == 20
    assert result["collection_pipeline"]["missing_isbn"] == 20
    assert result["collection_pipeline"]["author_count"] == 20


def test_browse_rejects_cursor_before_any_http():
    import httpx
    import pytest
    from ecole_loisirs_youth import EcoleLoisirsYouthSource
    requests = []
    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: requests.append(req) or httpx.Response(200, text="<main>empty</main>")
    )))
    for cursor in ("-1", "0x12", "101", "1/../livre/secret"):
        with pytest.raises(ValueError):
            source.browse(text_filter="loup", cursor=cursor)
    assert requests == []

def test_browse_and_advanced_romans_routes_coexist_without_extra_get():
    import httpx
    from ecole_loisirs_youth import EcoleLoisirsYouthSource
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/catalogue/romans":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                '<div class="product-item"><a href="/livre/roman-b">'
                '<span class="card-title">Roman B</span></a>'
                '<div class="card-infos">Age : 13 ans et +</div></div>'
            ))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            '<div>13 ans et +</div><a href="/livre/roman-a">Roman A</a>'
            '<div>roman</div><div>ISBN: 9782211345613</div>'
        ))

    source = EcoleLoisirsYouthSource(client=httpx.Client(transport=httpx.MockTransport(handle)))
    page = source.browse(text_filter="roman")
    advanced = source.search("roman", cursor="romans:2")
    assert len(page["items"]) == len(advanced["items"]) == 1
    assert seen[0].url.path == "/catalogue/romans"
    assert seen[1].url.path == "/recherche-avancee/romans"
    assert seen[1].url.params["page"] == "2"
    assert len(seen) == 2
