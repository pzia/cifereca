from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.catalogue_link import catalogue_url, safe_document_link_url


@pytest.mark.parametrize("unsafe", [
    " https://catalogue.example/book/1",
    "https://catalogue.example/book/1 ",
    "https://catalogue.example/book/1\n",
    "https://catalogue.example/book/1\x7f",
    "https://catalogue.example/book 1",
    "https://catalogue.example/book\u00a01",
    "https://@catalogue.example/book/1",
    "https://user:@catalogue.example/book/1",
    "https://catalogue.example\\@evil.example/book/1",
    "https://catalogue.example/book/1?x=1;token=secret",
    "https://catalogue.example/book/1?%2574oken=secret",
    "https://catalogue.example/book/1?X-Amz-Signature=secret",
    "https://catalogue.example/book/1#access_token=secret",
    "https://catalogue.example/book/1#x=1;signature=secret",
    "https://catalogue.example/download",
    "https://catalogue.example/download/book",
    "https://catalogue.example/downloads/book",
    "https://catalogue.example/Pages/Compte/MesLivres.aspx",
    "https://catalogue.example/Pages/Compte/readBook.ashx?lid=1",
    "https://catalogue.example/fulfillment/book.acsm",
    "https://catalogue.example/book%252Eepub",
])
def test_untrusted_public_links_are_refused_by_shared_url_validator(unsafe):
    assert catalogue_url({"friendly_url": unsafe}) is None
    assert safe_document_link_url(unsafe, relation="notice") is None


def test_safe_public_book_notice_keeps_legitimate_query_and_anchor():
    uri = "https://catalogue.example/notice/42?ref=library#section-2"
    assert catalogue_url({"friendly_url": uri}) == uri
    assert safe_document_link_url(uri, relation="notice") == uri


def test_catalogue_root_is_allowed_only_for_generic_links():
    uri = "https://catalogue.example/"
    assert safe_document_link_url(uri, relation="notice") is None
    assert safe_document_link_url(uri, relation="catalogue") == uri


def test_candidate_fallback_keeps_next_attested_public_url():
    row = {
        "friendly_url": "https://catalogue.example/loan/secure",
        "metadata": {"search_result": {"friendly_url": "https://catalogue.example/notice/42"}},
    }
    assert catalogue_url(row) == "https://catalogue.example/notice/42"


@pytest.mark.parametrize("metadata", [None, [], "bad metadata", {"search_result": []}])
def test_untrusted_metadata_shapes_do_not_crash_public_link_projection(metadata):
    assert catalogue_url({"metadata": metadata, "friendly_url": "https://catalogue.example/book/1"}) == (
        "https://catalogue.example/book/1"
    )


def test_detail_renderer_applies_validation_to_direct_plugin_links():
    main = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    module = ast.parse(main.read_text(encoding="utf-8"))
    detail = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    renderer = next(node for node in detail.body if isinstance(node, ast.FunctionDef) and node.name == "show_presentation")
    source = ast.unparse(renderer)
    assert "safe_document_link_url(link.url, relation=link.relation)" in source
    assert "if not uri or uri in self._rendered_document_urls:" in source
    assert "Gtk.LinkButton(uri=uri, label=str(link.label))" in source
