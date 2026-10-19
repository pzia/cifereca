"""OPDS 2.0 BnF Prêt contract fixtures: zero network, zero credentials."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE = (Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret"
          / "cifereca_bnf_pret_opds2.py")
spec = importlib.util.spec_from_file_location("cifereca_bnf_pret_opds2", MODULE)
opds = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = opds
spec.loader.exec_module(opds)


def publication(title="Titre"):
    return {
        "metadata": {"title": title, "identifier": "urn:isbn:9781234567897",
                     "author": [{"name": "A. Auteur"}, {"name": "B. Auteur"}],
                     "publisher": {"name": "Éditeur"}},
        "images": [{"href": "https://assets.cantook.net/assets/books/1/cover.jpg",
                    "type": "image/jpeg"}],
        "links": [
            {"rel": "http://opds-spec.org/acquisition/borrow",
             "href": "https://pret.bnf.fr/v1/loans/secret?token=PRIVATE",
             "type": "application/vnd.adobe.adept+xml"}
        ],
    }


def feed(**kwargs):
    raw = {
        "metadata": {"title": "BnF-Prêt numérique"},
        "links": [
            {"rel": "self", "type": "application/opds+json", "href": "/v1/home.opds2"},
            {"rel": ["next"], "type": "application/opds+json", "href": "/v1/selection.opds2?page=2"},
            {"rel": "search", "type": "application/opds+json",
             "templated": True, "href": "/v1/search.opds2{?query,title,author}"},
        ],
        "navigation": [{"title": "Catalogue", "type": "application/opds+json",
                        "href": "/v1/catalog.opds2"}],
        "publications": [publication()],
    }
    raw.update(kwargs)
    return raw


def test_parse_mixed_feed_and_no_acquisition_permission():
    parsed = opds.parse_catalog(feed())
    assert parsed.title == "BnF-Prêt numérique"
    assert parsed.next_url == "https://pret.bnf.fr/v1/selection.opds2?page=2"
    assert parsed.navigation == (opds.Navigation("Catalogue", "https://pret.bnf.fr/v1/catalog.opds2"),)
    book = parsed.publications[0]
    assert book.author == "A. Auteur, B. Auteur"
    assert book.publisher == "Éditeur"
    assert book.identifier == "urn:isbn:9781234567897"
    assert book.owned is False and book.borrowable is False
    assert book.cover_url == "https://assets.cantook.net/assets/books/1/cover.jpg"
    assert "PRIVATE" not in repr(parsed) and "/loans/" not in repr(parsed)


def test_groups_navigation_and_publications():
    result = opds.parse_catalog(feed(
        navigation=[], publications=[],
        groups=[{"metadata": {"title": "À la une"},
                 "navigation": [{"title": "Romans", "href": "/v1/romans.opds2", "type": "application/opds+json"}]},
                {"metadata": {"title": "Coups de cœur"}, "publications": [publication("Livre B")]}]
    ))
    assert result.navigation[0].title == "Romans"
    assert result.publications[0].title == "Livre B"


def test_empty_or_invalid_catalog_is_rejected():
    with pytest.raises(opds.OPDS2ContractError, match="empty_catalog"):
        opds.parse_catalog(feed(navigation=[], publications=[]))
    with pytest.raises(opds.OPDS2ContractError, match="invalid_catalog"):
        opds.parse_catalog({})
    with pytest.raises(opds.OPDS2ContractError, match="invalid_catalog"):
        opds.parse_catalog(["not an object"])
    with pytest.raises(opds.OPDS2ContractError, match="invalid_catalog"):
        opds.parse_catalog(feed(metadata={"title": "<img onerror=evil>"}))


@pytest.mark.parametrize("status,code", [(401, "auth_required"),
                                         (403, "access_forbidden"),
                                         (429, "rate_limited"),
                                         (503, "upstream_unavailable"),
                                         (302, "unexpected_status")])
def test_network_status_interpretation_without_request(status, code):
    with pytest.raises(opds.OPDS2ContractError) as error:
        opds.parse_catalog(feed(), status=status)
    assert error.value.code == code


@pytest.mark.parametrize("content_type", ["text/html", "application/json", "application/atom+xml"])
def test_login_html_or_other_protocol_cannot_be_parsed(content_type):
    with pytest.raises(opds.OPDS2ContractError, match="invalid_content_type"):
        opds.parse_catalog(feed(), content_type=content_type)
    assert opds.parse_catalog(feed(), content_type="application/opds+json; charset=utf-8")


def test_anonymous_feed_does_not_claim_user_rights_or_implicit_login():
    parsed = opds.parse_catalog(feed(links=[
        {"rel": "authenticate", "href": "https://pret.bnf.fr/v1/authentication?secret=KEY"},
        {"rel": "self", "href": "/v1/home.opds2", "type": "application/opds+json"}]))
    assert parsed.authentication_advertised is True
    assert not parsed.publications[0].borrowable
    assert "KEY" not in repr(parsed)


def test_search_template_expansion_is_query_only_and_percent_encodes():
    raw = opds.parse_catalog(feed())
    assert raw.search_template == "/v1/search.opds2{?query,title,author}"
    assert opds.expand_search(raw.search_template, "Victor Hugo & amis") == (
        "https://pret.bnf.fr/v1/search.opds2?query=Victor+Hugo+%26+amis"
    )


@pytest.mark.parametrize("template", [
    "https://attacker.example/v1/search{?query}",
    "http://pret.bnf.fr/v1/search{?query}",
    "//pret.bnf.fr.evil.test/v1/search{?query}",
    "/login{?query}",
    "/v1/loan{?token,query}",
    "/v1/search{?title}",
    "/v1/search{?query,query}",
    "/v1/search{?query}{evil}",
])
def test_untrusted_search_template_not_exposed(template):
    parsed = opds.parse_catalog(feed(links=[
        {"rel": "search", "type": "application/opds+json",
         "templated": True, "href": template}]))
    assert parsed.search_template is None


def test_non_templated_search_not_assumed():
    parsed = opds.parse_catalog(feed(links=[
        {"rel": "search", "type": "application/opds+json", "href": "/v1/search"}]))
    assert parsed.search_template is None


def test_externally_hosted_navigation_and_next_refused():
    parsed = opds.parse_catalog(feed(
        navigation=[{"title": "Externe", "href": "https://outside.test/v1/a",
                     "type": "application/opds+json"}],
        links=[{"rel": "next", "href": "//outside.test/v1/a", "type": "application/opds+json"}]))
    assert parsed.navigation == ()
    assert parsed.next_url is None


@pytest.mark.parametrize("bad_href", [
    "/v1/home.opds2?token=SECRET",
    "/v1/home.opds2#frag",
    "https://pret.bnf.fr@evil.example/v1/home.opds2",
    "http://pret.bnf.fr/v1/home.opds2",
    "https://pret.bnf.fr:444/v1/home.opds2",
    "/v1/../../login",
    "/v1/home.opds2?page=2&password=no",
    "/v1/home.opds2?query=",
])
def test_only_safe_navigation_urls(bad_href):
    result = opds.parse_catalog(feed(
        navigation=[{"title": "Bad link", "type": "application/opds+json", "href": bad_href}],
        links=[{"rel": "next", "type": "application/opds+json", "href": bad_href}]))
    assert not result.navigation
    assert result.next_url is None


@pytest.mark.parametrize("cover", [
    "https://assets.cantook.net.evil.test/assets/books/cover.jpg",
    "https://assets.cantook.net/assets/books/cover.jpg?token=private",
    "https://pret.bnf.fr/v1/loans/cover.jpg",
    "http://assets.cantook.net/assets/books/cover.jpg",
])
def test_never_leaks_unsafe_cover_urls(cover):
    item = publication()
    item["images"][0]["href"] = cover
    result = opds.parse_catalog(feed(publications=[item]))
    assert result.publications[0].cover_url is None
    assert "private" not in repr(result)


def test_feed_entry_budgets_prevent_exhaustion():
    too_many = [publication("Livre")] * 1001
    with pytest.raises(opds.OPDS2ContractError, match="invalid_publications"):
        opds.parse_catalog(feed(publications=too_many))


def test_untrusted_book_metadata_no_html_in_projection():
    item = publication()
    item["metadata"]["title"] = "<b>Injected</b>"
    parsed = opds.parse_catalog(feed(publications=[item]))
    assert not parsed.publications


def test_no_network_or_acquisition_methods_exposed():
    assert not any(hasattr(opds, method) for method in
                   ("borrow", "download", "return_loan", "login", "http_get", "connect"))
