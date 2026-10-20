from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.catalogue_link import catalogue_notice_url
from cifereca.plugin_api import document_links
from cifereca.services import LibraryService


def _service_host(provider="other", base="https://books.example/"):
    return NS(
        provider_descriptors=lambda: [NS(id=provider, homepage=base)],
        config=NS(get=lambda key, default: "https://public.example/"),
    )


def _target(host, row):
    return LibraryService.public_catalogue_target(host, row)


def test_attested_public_book_notice_is_not_replaced_by_homepage():
    host = _service_host()
    target = _target(host, {
        "provider_id": "other",
        "title": "Livre",
        "friendly_url": "https://books.example/notices/123",
        "download_url": "https://cdn.example/fulfillment/book.acsm",
    })
    assert target == ("notice", "https://books.example/notices/123")
    assert LibraryService.public_catalogue_url(host, {
        "provider_id": "other",
        "friendly_url": "https://books.example/notices/123",
    }) == target[1]


@pytest.mark.parametrize("friendly", [None, "", "https://books.example", "https://books.example/"])
def test_provider_homepage_is_a_generic_catalogue_not_a_book_notice(friendly):
    host = _service_host()
    row = {"provider_id": "other", "title": "Livre", "friendly_url": friendly}
    assert _target(host, row) == ("catalogue", "https://books.example/")
    assert document_links(row) == ()


def test_generic_url_precedes_explicit_book_product_url():
    host = _service_host()
    row = {
        "provider_id": "other",
        "friendly_url": "https://books.example/",
        "metadata": {"product_url": "https://books.example/book/456"},
    }
    assert _target(host, row) == ("notice", "https://books.example/book/456")
    assert document_links(row)[0].url == "https://books.example/book/456"


def test_relative_notice_uses_provider_base_but_never_converts_homepage_to_notice():
    host = NS(
        provider_descriptors=lambda: [NS(id="paris", homepage="https://old.example/")],
        config=NS(paris_base_url=lambda credential: "https://paris.example/",
                  get=lambda key, default: "https://public.example/"),
    )
    row = {"source_provider": "paris", "source_credential": "one",
           "friendly_url": "/notice/42"}
    assert _target(host, row) == ("notice", "https://paris.example/notice/42")
    assert _target(host, {**row, "friendly_url": "/"}) == ("catalogue", "https://paris.example/")


def test_private_numilog_library_index_is_not_a_public_book_notice():
    host = _service_host("numilog", "https://www.numilog.com/")
    row = {
        "provider_id": "numilog",
        "friendly_url": "https://www.numilog.com/Pages/Compte/MesLivres.aspx",
        "title": "Roman",
    }
    assert _target(host, row) == ("catalogue", "https://www.numilog.com/")
    assert document_links(row) == ()


def test_unknown_source_uses_generic_fallback_without_fabricating_notice():
    host = NS(
        provider_descriptors=lambda: [],
        config=NS(get=lambda key, default: "https://catalogue.bnf.fr/"),
    )
    assert _target(host, {"provider_id": "local", "title": "Roman"}) == (
        "catalogue", "https://catalogue.bnf.fr/"
    )


@pytest.mark.parametrize("url", [
    "file:///tmp/book",
    "javascript:alert(1)",
    "https://cdn.example/book.epub",
    "https://cdn.example/book.acsm",
    "https://cdn.example/fulfillment/123",
    "https://cdn.example/loan/123",
    "https://catalogue.example/book?token=secret",
    "https://catalogue.example/book?X-Amz-Signature=secret",
    "https://user:secret@catalogue.example/book",
    "https://catalogue.example:444/book",
    "https://catalogue.example/book\n",
    "/book\n",
])
def test_delivery_signed_and_unsafe_links_are_not_presented_as_notices(url):
    host = _service_host()
    row = {"provider_id": "other", "friendly_url": url}
    assert catalogue_notice_url(row, base="https://books.example/") is None
    assert _target(host, row) == ("catalogue", "https://books.example/")
    assert document_links(row) == ()


def test_document_link_explicit_metadata_never_relabels_generic_homepage():
    row = {
        "source_provider": "numilog",
        "metadata": {"document_links": [
            {"relation": "notice", "url": "https://www.numilog.com/Pages/Compte/MesLivres.aspx"},
            {"relation": "notice", "url": "https://public.example/notice/999"},
        ]},
    }
    links = document_links(row)
    assert len(links) == 1
    assert links[0].url == "https://public.example/notice/999"


def test_ui_action_distinguishes_book_notice_from_generic_catalogue():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    owner = ast.parse(path.with_name("public_catalogue_action_controller.py").read_text(encoding="utf-8"))
    method = next(n for n in owner.body
                  if isinstance(n, ast.FunctionDef) and n.name == "add_catalogue_action")
    source = ast.unparse(method)
    assert "public_catalogue_target(item)" in source
    assert "'Ouvrir la notice'" in source
    assert "'Ouvrir le catalogue'" in source
    assert "current[0] == kind" in source
    assert "_rendered_document_urls" in source
    main = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_add_catalogue_action")
    assert "public_catalogue_action_controller.add_catalogue_action" in ast.unparse(facade)


# Regression #2460: the old EbooksGratuits COPS provider persisted a
# search/feed URL in friendly_url if no HTML alternate was available.
# The provider now returns None for new records; Core must refuse stale
# cached rows, too, without a network call or destructive cache migration.
@pytest.mark.parametrize("xml_link", [
    "https://opds2.ebooksgratuits.org/index.php/feed",
    "https://opds2.ebooksgratuits.org/index.php/feed/search?q=roman",
    "https://opds2.ebooksgratuits.org/index.php/%66eed/search?q=roman",
    "https://opds2.ebooksgratuits.org/index.php/%2566eed/search?q=roman",
    "https://opds2.ebooksgratuits.org/index.php/opensearch.xml",
    "https://opds2.ebooksgratuits.org/feed",
    "https://opds2.ebooksgratuits.org/index.php/fetch/42",
    "https://opds2.ebooksgratuits.org/index.php/fetch/42.epub",
    "https://opds2.ebooksgratuits.org/book/42.atom",
    "https://opds2.ebooksgratuits.org/book/42.opds",
])
def test_cached_ebooksgratuits_opds_routes_are_not_html_notices(xml_link):
    host = _service_host("ebooksgratuits",
                         "https://opds2.ebooksgratuits.org/")
    row = {
        "provider_id": "ebooksgratuits",
        "title": "Exemple public",
        "friendly_url": xml_link,
        "metadata": {
            "acquisition_source": "opds-calibre",
            "product_url": xml_link,
        },
    }
    assert catalogue_notice_url(row, provider="ebooksgratuits") is None
    assert document_links(row) == ()
    assert _target(host, row) == (
        "catalogue", "https://opds2.ebooksgratuits.org/"
    )


def test_cached_xml_link_does_not_hide_independent_attested_html_alternate():
    host = _service_host("ebooksgratuits",
                         "https://opds2.ebooksgratuits.org/")
    html = "https://opds2.ebooksgratuits.org/index.php/page/book/42"
    row = {
        "provider_id": "ebooksgratuits",
        "friendly_url": "https://opds2.ebooksgratuits.org/index.php/feed/search?q=42",
        "metadata": {
            "product_url": html,
            "acquisition_source": "opds-calibre",
        },
    }
    assert _target(host, row) == ("notice", html)
    assert [link.url for link in document_links(row)] == [html]


def test_document_links_drop_cached_opds_xml_but_preserve_html():
    xml = "https://opds2.ebooksgratuits.org/index.php/feed/item/42"
    html = "https://opds2.ebooksgratuits.org/index.php/page/book/42"
    row = {
        "source_provider": "ebooksgratuits",
        "metadata": {
            "acquisition_source": "opds-calibre",
            "document_links": [
                {"relation": "notice", "url": xml, "label": "Voir en ligne"},
                {"relation": "notice", "url": html, "label": "Ouvrir la notice"},
            ],
        },
    }
    assert [(link.label, link.url) for link in document_links(row)] == [
        ("Ouvrir la notice", html),
    ]


def test_legacy_ebooksgratuits_html_notice_is_still_public():
    host = _service_host("ebooksgratuits",
                         "https://www.ebooksgratuits.com/")
    detail_url = "https://www.ebooksgratuits.com/details.php?book=42"
    row = {"source_provider": "ebooksgratuits",
           "friendly_url": detail_url}
    assert _target(host, row) == ("notice", detail_url)
    assert document_links(row)[0].url == detail_url
