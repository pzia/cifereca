"""Synthetic Book schema evidence. No provider HTML or account information."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load("biblioaccess_observations")
mod = _load("biblioaccess_metadata")
URL = "https://www.biblioaccess.com/1409/Catalog/Book/123/a-title"


def test_typed_book_fields_not_loan_or_delivery():
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Book","name":"Fiction synthétique",
    "author":[{"@type":"Person","name":"A. Exemple"}],
    "publisher":{"@type":"Organization","name":"Éditeur fictif"},
    "isbn":"9782818061435",
    "image":"https://www.biblioaccess.com/images/cover.png",
    "datePublished":"2024-03-01",
    "potentialAction":{"@type":"BorrowAction"}}
    </script></head><body>Vous pouvez emprunter</body></html>"""
    book = mod.parse_book_metadata(URL, html)
    assert book is not None
    assert book.book_id == "123"
    assert book.title == "Fiction synthétique"
    assert book.author == "A. Exemple"
    assert book.publisher == "Éditeur fictif"
    assert book.published_at == "2024-03-01"
    assert book.entitlement == "unverified"
    assert book.acquisition == "unsupported"
    assert book.image_url.endswith("cover.png")
    assert "BorrowAction" not in repr(book)


def test_missing_typed_metadata_does_not_invent_notice():
    assert mod.parse_book_metadata(URL, '<meta property="og:title" content="HTML">') is None
    assert mod.parse_book_metadata(URL, "<script type='application/ld+json'>{not-json}</script>") is None
    assert mod.parse_book_metadata(URL, '<script type="application/ld+json">{"@type":"Thing","name":"X"}</script>') is None


def test_foreign_image_rejected():
    html = """<script type="application/ld+json">
       {"@type":"Book","name":"Synthetic","image":"https://evil.example/covers/x"}
    </script>"""
    assert mod.parse_book_metadata(URL, html).image_url is None


@pytest.mark.parametrize("url", [
    "https://www.biblioaccess.com/1717/Catalog/Book/123/title",
    "http://www.biblioaccess.com/1409/Catalog/Book/123/title",
    "https://evil.example/1409/Catalog/Book/123/title",
    "https://www.biblioaccess.com/1409/Catalog/Book/123/title?ticket=SYNTHETIC",
])
def test_invalid_book_scope(url):
    with pytest.raises(ValueError):
        mod.parse_book_metadata(url, '<script type="application/ld+json">{"@type":"Book","name":"X"}</script>')


def test_oversized_jsonld_does_not_fallback():
    html = ('<script type="application/ld+json">' +
            " " * 100001 +
            '{"@type":"Book","name":"Forged"}</script>')
    assert mod.parse_book_metadata(URL, html) is None


def test_invalid_isbn_is_not_inferred():
    html = '<script type="application/ld+json">{"@type":"Book","name":"X","isbn":"9780000000000"}</script>'
    assert mod.parse_book_metadata(URL, html).isbn13 is None
