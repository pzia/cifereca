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
