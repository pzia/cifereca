"""#946: offline contract examples, not archived real Milan HTML captures."""
import httpx
import pytest
from milan_youth import MilanYouthSource, parse_detail, search_next_cursor, search_url, product_url

PRODUCT = "https://www.editionsmilan.com/livres/60263-cest-quoi-lecologie-des-9-ans/"
COVER = ("https://imagine.bayard.io/unsafe/560x0/bayard-static/"
         "edition/couvertures/9782408060886/9782408060886.jpg")
DETAIL = """
<h1>C'est quoi, l'écologie ?</h1>
<div>Auteur</div><div>Sophie Dussaussois</div>
<div>Illustrateur</div><div>Jacques Azam</div>
<div>Collection</div><div>C'est quoi ?</div>
<div>Âge</div><div>À partir de 9 ans</div>
<h2>Caractéristiques</h2><div>Code ISBN</div><div>978-2-408-06088-6</div>
<div>Thèmes</div><div>biodiversité, écologie, nature, pollution</div>
<div>Date de parution</div><div>26/08/2025</div>
<div>Nombre de pages</div><div>64</div>
<div>Prix</div><div>6,90 €</div><div>Prix numérique</div><div>5,00 €</div>
<div>Résumé</div><p>Un livre sur l'écologie.</p>
""" + '<img alt="Couverture du livre" src="' + COVER + '">'

def test_remote_search_qualifies_detail_even_when_query_not_in_title():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/recherche/books/":
            assert dict(request.url.params) == {"text": "loup"}
            html = '<a href="/livres/60263-cest-quoi-lecologie-des-9-ans/">C\'est quoi, l\'écologie ?</a>'
        else:
            assert str(request.url) == PRODUCT
            html = DETAIL
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.search("loup")
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["isbn13"] == "9782408060886"
    assert row["authors"] == ("Sophie Dussaussois",)
    assert row["illustrators"] == ("Jacques Azam",)
    assert row["cover_url"] == COVER
    assert row["description"] == "Un livre sur l'écologie."
    assert row["published_year"] == 2025
    assert row["metadata"]["pages"] == 64
    assert row["metadata"]["publication_date"] == "2025-08-26"
    assert row["metadata"]["price"] == "6,90 €"
    assert row["metadata"]["digital_price"] == "5,00 €"
    assert row["age_ranges"][0]["age_min"] == 9
    assert result["collection_pipeline"]["query_mode"] == "remote"
    assert result["collection_request_count"] == 2
    assert seen[0] == search_url("loup")

def test_search_pagination_requires_link_and_exact_same_query():
    page2 = "/recherche/books/?text=loup&page=2"
    html = '<a href="' + page2 + '">Page suivante</a>'
    assert search_next_cursor(html, query="loup", current_url=search_url("loup")).endswith(page2)
    assert not search_next_cursor('<a href="/recherche/books/?text=autre&page=2">2</a>',
                                  query="loup", current_url=search_url("loup"))
    assert not search_next_cursor("<p>Sans suite</p>", query="loup", current_url=search_url("loup"))
    seen = []
    def handler(request):
        seen.append(str(request.url))
        text = html if "page" not in request.url.params else "<p>fin</p>"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=text)
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    first = source.search("loup")
    second = source.search("loup", cursor=first["next_cursor"])
    assert second["items"] == () and second["next_cursor"] == ""
    with pytest.raises(ValueError, match="cursor"):
        source.search("loup", cursor=first["next_cursor"].replace("loup", "autre"))
    assert len(seen) == 2

def test_dedupe_on_isbn_and_max_eight_product_requests():
    links = "".join(f'<a href="/livres/{60263+i}-duplicate/">Voir le livre</a>'
                    for i in range(12))
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=links if request.url.path == "/livres/" else DETAIL)
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse()
    assert len(result["items"]) == 1
    assert len(seen) == 9
    assert result["collection_pipeline"]["rejection_reasons"]["duplicate"] == 7

def test_explicit_age_isbn_and_public_cover_only():
    assert parse_detail(DETAIL.replace("978-2-408-06088-6", "invalide"), url=PRODUCT) is None
    assert parse_detail(DETAIL.replace("À partir de 9 ans", "À partir de 18 ans"), url=PRODUCT) is None
    row = parse_detail(DETAIL.replace(COVER, "https://evil.example/?session=secret"), url=PRODUCT)
    assert row is not None and row["cover_url"] == ""
    assert product_url(PRODUCT + "?token=secret") == ""
