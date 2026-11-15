"""P1 #6168: one observed BiblioAccess MyLoans book anchor, one public image.

The fixture is synthetic and entirely offline: no claim about the user's
actual La Rochelle DOM or profile. An image is only descriptive loan data.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in ("biblioaccess_observations", "biblioaccess_delivery_url",
             "biblioaccess_loans_page"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)

from biblioaccess_loans_page import inspect_myloans_html

BOOK = "20002"
PRODUCT = "10001"
COVER = "https://www.biblioaccess.com/1409/Covers/book.png"
LOGOUT = '<form id="logoutForm" method="post" action="/1409/Account/LogOff"></form>'


def row(*, product=PRODUCT, book=BOOK, images=None, extra=""):
    if images is None:
        images = ['<img src="/1409/Covers/book.png">']
    return (
        '<div class="JSProductRow-' + product + '">'
        '<a href="/1409/Catalog/Book/' + book + '/notice">'
        + "".join(images) + '</a>'
        '<p class="titleLoans">SYNTHETIC-PRIVATE-BOOK</p>'
        '<span class="jsDateEnd">08/11/2026 12:30:00</span>'
        + extra + '</div>'
    )


def single(*, images=None, extra=""):
    result = inspect_myloans_html(LOGOUT + row(images=images, extra=extra))
    assert result.page_loan_row_count == 1
    assert result.page_loan_row_partial_count == 0
    assert result.logout_form_observed
    loan, = result._loan_rows
    assert loan.book_id == BOOK and loan.product_ref == PRODUCT
    return loan


def test_exact_same_book_anchor_supplies_public_cover_without_new_fetch():
    loan = single()
    assert loan.thumbnail_url == COVER
    assert loan.title == "SYNTHETIC-PRIVATE-BOOK"
    assert not loan.acsm_offer_present
    assert COVER not in repr(loan)
    assert COVER not in repr(inspect_myloans_html(LOGOUT + row()))


def test_missing_illustration_keeps_positive_loan_and_its_scope():
    loan = single(images=[])
    assert loan.thumbnail_url is None
    assert loan.book_id == BOOK


@pytest.mark.parametrize("src", [
    'https://foreign.example.org/book.png',
    'http://www.biblioaccess.com/1409/Covers/book.png',
    '//attacker.example.org/1409/Covers/book.png',
    'file:///tmp/private.png',
    'javascript:alert(1)',
    '/1409/Covers/book.png?token=PRIVATE',
    '/1409/Covers/book.png#fragment',
    '/1409/Covers/book.png?width=90',
    '/1409/Account/private.jpg',
    '/1409/Download/current.jpg',
    '/1409/Catalog/MyLoans/private.jpg',
    '/1409/Covers/book.svg',
    '/1409/Covers/book.webp?signature=PRIVATE',
    'https://www.biblioaccess.com:444/1409/Covers/book.jpg',
    '/1409/Covers/%2e%2e/book.png',
    '',
])
def test_untrusted_or_unqualified_image_is_ignored_without_losing_loan(src):
    loan = single(images=['<img src="' + src + '">'])
    assert loan.thumbnail_url is None


def test_outside_row_or_book_anchor_never_supplies_image():
    content = (
        '<img src="/1409/Covers/outside.png">'
        + LOGOUT
        + row(images=[])
        + '<img src="/1409/Covers/other.png">'
    )
    observed = inspect_myloans_html(content)
    assert observed._loan_rows[0].thumbnail_url is None

    # The same row's branding image outside the exact Book link is not proof.
    observed = inspect_myloans_html(LOGOUT + row(
        images=[], extra='<img src="/1409/Covers/branding.png">'
    ))
    assert observed._loan_rows[0].thumbnail_url is None


def test_two_different_images_from_same_book_anchor_are_ambiguous():
    loan = single(images=[
        '<img src="/1409/Covers/a.jpg">',
        '<img src="/1409/Covers/b.png">',
    ])
    assert loan.thumbnail_url is None


def test_repeat_same_image_does_not_create_fake_conflict():
    loan = single(images=[
        '<img src="/1409/Covers/a.jpg">',
        '<img src="https://www.biblioaccess.com/1409/Covers/a.jpg">',
    ])
    assert loan.thumbnail_url == "https://www.biblioaccess.com/1409/Covers/a.jpg"


def test_bounded_same_book_image_observation_does_not_drop_loan():
    images = [
        '<img src="/1409/Covers/' + str(i) + '.png">'
        for i in range(9)
    ]
    loan = single(images=images)
    assert loan.thumbnail_url is None


def test_images_for_two_distinct_rows_remain_independent_despite_equal_title():
    observed = inspect_myloans_html(
        LOGOUT + row(product="10001", book="20002", images=[
            '<img src="/1409/Covers/first.png">'
        ]) + row(product="10002", book="20003", images=[
            '<img src="/1409/Covers/second.png">'
        ])
    )
    assert len(observed._loan_rows) == 2
    assert [x.thumbnail_url for x in observed._loan_rows] == [
        "https://www.biblioaccess.com/1409/Covers/first.png",
        "https://www.biblioaccess.com/1409/Covers/second.png",
    ]


def test_another_book_anchor_in_one_row_is_not_a_valid_loan():
    observed = inspect_myloans_html(LOGOUT + row(
        extra=(
            '<a href="/1409/Catalog/Book/99999/foreign">'
            '<img src="/1409/Covers/foreign.jpg"></a>'
        ),
    ))
    assert observed.page_loan_row_count == 0
    assert observed.page_loan_row_partial_count == 1


def test_broken_book_link_must_not_upgrade_a_matching_image_to_loan_proof():
    observed = inspect_myloans_html(LOGOUT + (
        '<div class="JSProductRow-10001">'
        '<a href="/1409/Catalog/Book/20002/notice"></a>'
        '<img src="/1409/Covers/book.png">'
        '<p class="titleLoans">Synthetic</p>'
        '<span class="jsDateEnd">08/11/2026 12:30:00</span>'
        '</div>'
    ))
    assert observed._loan_rows[0].thumbnail_url is None


def test_observation_is_not_an_entitlement_or_delivery_token():
    loan = single(images=['<img src="/1409/Covers/book.png">'])
    assert loan.acsm_url is None
    assert loan.delivery_kind is None
    assert loan.thumbnail_url == COVER
    assert "PRIVATE" not in repr(loan)
