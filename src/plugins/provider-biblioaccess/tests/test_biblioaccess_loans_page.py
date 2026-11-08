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


def _observed_row(*, product="10001", book="20002", due="08/11/2026 12:30:00",
                  title="PRIVATE-SYNTHETIC-BOOK", acsm=True):
    offer = (
        '<div class="downloadbook JSdownloadbook-' + product + '">'
        '<a href="https://entrepot2r.numilog.com/fulfillment/URLLink.acsm'
        '?token=PRIVATE-SYNTHETIC-ACSM">Télécharger</a></div>'
        if acsm else ""
    )
    return (
        '<div class="row align-items-center JSProductRow-' + product + '">'
        '<div><a href="/1409/Catalog/Book/' + book + '/private-book">'
        '<img src="/cover.jpg"></a></div>'
        '<div><p class="mb-1 titleLoans">' + title + '</p>'
        '<span class="LblLabel jsDateEnd">' + due + '</span>'
        + offer + '</div></div>'
    )


def test_private_capture_shape_yields_one_positive_row_without_entitlement():
    html = (
        '<form id="logoutForm" method="post" action="/1409/Account/LogOff"></form>'
        + _observed_row()
        + '<a href="/1409/Catalog/Book/30003/recommendation">public</a>'
    )
    evidence = loans.inspect_myloans_html(html)
    assert evidence.page_loan_row_count == 1
    assert evidence.page_loan_row_partial_count == 0
    assert evidence.logout_form_observed is True
    assert evidence.page_book_unique_count == 2  # Book links != loans
    row = evidence._loan_rows[0]
    assert row.product_ref == "10001"
    assert row.book_id == "20002"
    assert row.title == "PRIVATE-SYNTHETIC-BOOK"
    assert row.due_at_local == "08/11/2026 12:30:00"
    assert row.acsm_offer_present is True
    assert not evidence.inventory_verified and not evidence.identity_verified
    assert evidence.completeness == "unknown"
    assert "PRIVATE" not in repr(evidence)
    assert "PRIVATE" not in repr(row)
    assert "10001" not in repr(row) and "20002" not in repr(row)


def test_outside_row_recommendations_and_logout_form_do_not_create_loan():
    evidence = loans.inspect_myloans_html(
        '<form id="logoutForm" method="post" action="/1409/Account/LogOff"></form>'
        '<a href="/1409/Catalog/Book/20002/rec">catalog</a>')
    assert evidence.page_book_unique_count == 1
    assert evidence.page_loan_row_count == 0
    assert evidence.logout_form_observed
    assert not evidence.identity_verified


@pytest.mark.parametrize("broken_html", [
    _observed_row(due="31/02/2026 12:30:00"),
    _observed_row(due="missing"),
    _observed_row(title=""),
    '<div class="JSProductRow-10001"><p class="titleLoans">Private</p>'
    '<span class="jsDateEnd">08/11/2026 12:30:00</span>'
    '<a href="/1717/Catalog/Book/20002/other-tenant">invalid</a></div>',
    _observed_row() .replace(
        'href="/1409/Catalog/Book/20002/private-book"',
        'href="https://evil.example/1409/Catalog/Book/20002/private-book"'),
])
def test_ambiguous_or_incomplete_row_never_qualifies_a_loan(broken_html):
    evidence = loans.inspect_myloans_html(broken_html)
    assert evidence.page_loan_row_count == 0
    assert evidence.page_loan_row_partial_count == 1
    assert not evidence.inventory_verified


def test_independent_product_ref_is_not_conflated_with_book_or_other_loans():
    evidence = loans.inspect_myloans_html(
        _observed_row(product="10001", book="20002")
        + _observed_row(product="10003", book="20002", acsm=False))
    assert evidence.page_loan_row_count == 2
    assert evidence.page_loan_row_partial_count == 0
    assert [row.product_ref for row in evidence._loan_rows] == ["10001", "10003"]
    assert all(row.book_id == "20002" for row in evidence._loan_rows)
    assert [row.acsm_offer_present for row in evidence._loan_rows] == [True, False]
    assert not evidence.inventory_verified


def test_acsm_action_host_must_be_exact_and_link_is_never_retained():
    malicious = _observed_row().replace(
        "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm",
        "https://entrepot2r.numilog.com.attacker.invalid/fulfillment/URLLink.acsm",
    )
    observed = loans.inspect_myloans_html(malicious)
    assert observed.page_loan_row_count == 1
    assert observed._loan_rows[0].acsm_offer_present is False
    assert "PRIVATE-SYNTHETIC-ACSM" not in repr(observed)


def test_nested_row_refused_without_inventing_exhaustive_inventory():
    with pytest.raises(loans.LoanPageError, match="loans_page_nested_row"):
        loans.inspect_myloans_html(
            '<div class="JSProductRow-10001">'
            '<div class="JSProductRow-10002"></div></div>'
        )


def test_unclosed_real_row_refused_as_truncated_html():
    with pytest.raises(loans.LoanPageError, match="loans_page_row_unclosed"):
        loans.inspect_myloans_html(
            '<div class="JSProductRow-10001"><p class="titleLoans">Private</p>'
        )


def test_duplicate_product_ref_is_ambiguous_even_if_only_one_row_is_valid():
    """A second row must not make the first product/ref appear authoritative."""
    with pytest.raises(loans.LoanPageError, match="loans_page_duplicate_product_ref"):
        loans.inspect_myloans_html(
            _observed_row(product="10001", book="20002")
            + _observed_row(product="10001", book="30003", acsm=False)
        )


def test_duplicate_product_ref_cannot_be_hidden_in_a_partial_row():
    with pytest.raises(loans.LoanPageError, match="loans_page_duplicate_product_ref"):
        loans.inspect_myloans_html(
            _observed_row(product="10001", book="20002")
            + _observed_row(product="10001", book="99999", due="invalid")
        )


def test_duplicate_product_ref_even_with_identical_content_is_unverified():
    with pytest.raises(loans.LoanPageError, match="loans_page_duplicate_product_ref"):
        loans.inspect_myloans_html(_observed_row() * 2)
