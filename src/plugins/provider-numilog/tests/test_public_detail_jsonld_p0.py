"""Offline security/contract tests for Numilog public product JSON-LD (#1886, #1072)."""
import json
import pytest

from cifereca_numilog_public_detail import parse_public_detail_jsonld, PublicDetailError

URL = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"


def page(book):
    return '<html><script type="application/ld+json">' + json.dumps(book) + '</script></html>'


def book(**overrides):
    result = {"@context": "https://schema.org", "@type": "Book", "url": URL,
              "name": "Livre fictif", "author": [{"@type": "Person", "name": "Alice"}],
              "isbn": "9782075241939", "publisher": {"name": "Éditeur fictif"}}
    result.update(overrides)
    return result


def test_extracts_only_attested_metadata_without_digital_edition_or_ownership():
    result = parse_public_detail_jsonld(page(book()), URL)
    assert result["fields"]["title"] == "Livre fictif"
    assert result["fields"]["authors"] == ["Alice"]
    assert result["fields"]["publisher"] == "Éditeur fictif"
    assert result["owned"] is False and result["downloadable"] is False
    assert result["download_url"] is None
    assert "isbn_digital" not in result["fields"]
    assert all(not evidence["edition_proven"] for evidence in result["field_evidence"].values())


@pytest.mark.parametrize("changed", [
    {"url": "https://www.numilog.com/1958477/autre.ebook"},
    {"url": "https://evil.example/1958476/book.ebook"},
    {"url": URL + "?auth=token"},
    {"@type": "Product"},
])
def test_rejects_nonmatching_or_unqualified_book(changed):
    with pytest.raises(PublicDetailError, match="missing_or_ambiguous_book"):
        parse_public_detail_jsonld(page(book(**changed)), URL)


def test_rejects_duplicate_books_and_does_not_choose_a_favorite():
    with pytest.raises(PublicDetailError, match="missing_or_ambiguous_book"):
        parse_public_detail_jsonld(page({"@graph": [book(), book()]}), URL)


def test_invalid_isbn_does_not_prove_any_edition():
    result = parse_public_detail_jsonld(page(book(isbn="9780000000000")), URL)
    assert "isbn_edition_unspecified" not in result["fields"]


def test_ignores_shell_secrets_and_purchase_links():
    shell = '<input value="PRIVATE_MARKER"><script>const secret = "PRIVATE_MARKER";</script>'
    result = parse_public_detail_jsonld(shell + page(book(offers={
        "@type": "Offer", "url": "https://example.test/download?token=PRIVATE_MARKER"
    })), URL)
    assert "PRIVATE_MARKER" not in repr(result)


@pytest.mark.parametrize("url", [
    URL + "?token=secret", URL.replace("https:", "http:"),
    "https://evil.example/1958476/jefferson-prend-des-risques.ebook",
])
def test_public_detail_url_is_strict(url):
    with pytest.raises(PublicDetailError, match="invalid_product_url"):
        parse_public_detail_jsonld(page(book()), url)


def test_rejects_bad_json_and_oversized_payload_without_leaking_it():
    with pytest.raises(PublicDetailError, match="invalid_jsonld") as caught:
        parse_public_detail_jsonld('<script type="application/ld+json">PRIVATE_MARKER</script>', URL)
    assert "PRIVATE_MARKER" not in str(caught.value)
    with pytest.raises(PublicDetailError, match="html_too_large"):
        parse_public_detail_jsonld("a" * (1024 * 1024 + 1), URL)
