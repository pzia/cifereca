"""Synthetic MyLoans candidate staging, never evidence of a real borrower."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in ("biblioaccess_observations", "biblioaccess_loans_page"):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)

loans = sys.modules["biblioaccess_loans_page"]


def test_stages_same_tenant_book_links_without_entitlement_or_personal_data():
    private = "SYNTHETIC-PRIVATE-NAME"
    html = (
        '<html><a href="/1409/Catalog/Book/12345/an-ebook">' + private + '</a>'
        '<a href="/1409/Catalog/Book/12345/an-ebook">duplicate</a>'
        '<a href="https://www.biblioaccess.com/1409/Catalog/Book/54321">second</a>'
        '<a href="/1717/Catalog/Book/987">foreign tenant</a>'
        '<a href="https://evil.example/1409/Catalog/Book/777">foreign host</a>'
        '<a href="/1409/Catalog/Book/555?ticket=SECRET">signed</a>'
        '<a href="/1409/Catalog/Book/12345/#fragment">fragment</a>'
        '</html>'
    )
    result = loans.inspect_myloans_html(html)
    assert result.page_book_link_count == 3
    assert result.page_book_unique_count == 2
    assert result._book_ids == ("12345", "54321")
    assert result._book_urls[0] == (
        "12345", "https://www.biblioaccess.com/1409/Catalog/Book/12345/an-ebook")
    assert not result.inventory_verified and not result.identity_verified
    assert result.completeness == "unknown"
    assert private not in repr(result)
    assert "12345" not in repr(result)
    assert "SECRET" not in repr(result)


def test_empty_or_public_html_is_unknown_not_empty_verified_inventory():
    result = loans.inspect_myloans_html(
        '<html><a href="/1409/">accueil</a></html>')
    assert result.page_book_link_count == 0
    assert result.page_book_unique_count == 0
    assert not result.inventory_verified
    assert result.completeness == "unknown"


def test_bounded_html_and_link_budget():
    with pytest.raises(loans.LoanPageError, match="loans_page_body_limit"):
        loans.inspect_myloans_html("x" * (256 * 1024 + 1))
    with pytest.raises(loans.LoanPageError, match="loans_page_anchor_limit"):
        loans.inspect_myloans_html('<a href="/1409/"></a>' * 513)
