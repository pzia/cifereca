"""#4118: narrow public Feedbooks covers observed on BnF HTML.

Synthetic identifiers and pages only. No browser, network image fetch,
credentials, loan mutation, ADEPT processing or private captured HTML.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))

from cifereca_bnf_pret_web import (
    ORIGIN, _public_cover_url, parse_search_page, parse_detail, parse_loans,
    ProfileScope,
)
from cifereca_bnf_pret import BnfProviderSession


FEED = "https://covers.feedbooks.net/item/synthetic-id_12345.jpg?size=medium&t=1762000000"
FEED_B = "https://covers.feedbooks.net/item/other-synthetic-id.jpg?size=small&t=1762000001"
CDN = "https://imagedelivery.net/QDkyDSqaJI1JEO0MqH_3SQ/d721a0b0-1425-45f0-6e08-aec9b2755500/default"


def card(cover=FEED, rid="book-a", *, extra=""):
    return (
        '<li class="list-details__item"><article class="view-details" '
        'itemscope itemtype="https://schema.org/Book">'
        f'<a href="/resources/{rid}"><h3 class="details-content__title">Livre synthétique</h3></a>'
        f'<img class="cover" src="{cover}">' + extra
        + '</article></li>'
    )


def search(*cards):
    return '<ul class="list-details">' + "".join(cards) + '</ul>'


def detail(cover=FEED, *, extra=""):
    return (
        '<article itemscope itemtype="https://schema.org/Book">'
        '<h1 itemprop="name">Livre synthétique</h1>'
        f'<img class="cover" itemprop="image" src="{cover}">'
        + extra + '</article>'
    )


def activity(cover=FEED):
    return (
        '<section class="active-loans"><div class="js-loan">'
        '<a href="/resources/book-a">Livre synthétique'
        f'<img class="cover" src="{cover}"></a>'
        '<a href="/loans/loan-a">Prêt</a></div></section>'
    )


class FeedbooksCoverTests(unittest.TestCase):
    def test_public_url_family_is_allowed_with_exact_query_only(self):
        for allowed in (
            FEED,
            FEED_B,
            "https://covers.feedbooks.net/item/123.jpg?t=17&size=small",
        ):
            with self.subTest(allowed=allowed):
                self.assertEqual(_public_cover_url(allowed), allowed)
        self.assertEqual(_public_cover_url(CDN), CDN)
        self.assertEqual(_public_cover_url("/covers/book-a.jpg"), ORIGIN + "/covers/book-a.jpg")

    def test_no_open_query_auth_or_host_extensions(self):
        bad = (
            FEED.replace("https://", "http://"),
            FEED.replace("covers.feedbooks.net", "covers.feedbooks.net.evil.invalid"),
            FEED.replace("covers.feedbooks.net", "covers.feedbooks.net:443"),
            FEED.replace("covers.feedbooks.net", "user@covers.feedbooks.net"),
            FEED.replace("/item/", "/loans/"),
            FEED.replace(".jpg?", ".png?"),
            FEED.replace(".jpg?", ".jpg/extra?"),
            FEED.replace("size=medium", "size=medium%20private"),
            FEED.replace("size=medium", "size=x_y"),
            FEED.replace("t=1762000000", "t=abc"),
            FEED.replace("t=1762000000", "t=1762000000&token=secret"),
            FEED + "&size=large",
            FEED + "#fragment",
            FEED.replace("/item/", "/item/../item/"),
            FEED.replace("/item/", "/item/%2e%2e/"),
            FEED.replace("/item/", "/item//"),
            FEED.replace("covers.feedbooks.net", "covers.feedbooks.net@evil.invalid"),
            "https://covers.feedbooks.net/item/no-query.jpg",
            "https://covers.feedbooks.net/item/abc.jpg?size=medium",
            "https://covers.feedbooks.net/item/abc.jpg?t=1",
            "https://pret.bnf.fr/covers/a.jpg?size=medium&t=123",
            CDN + "?size=medium&t=123",
        )
        for unsafe in bad:
            with self.subTest(unsafe=unsafe):
                self.assertIsNone(_public_cover_url(unsafe))

    def test_search_cards_keep_independent_cover_and_existing_cdn():
        records = parse_search_page(search(card(), card(CDN, "book-b"))).records
        self.assertEqual([(r.resource_id, r.thumbnail_url) for r in records],
                         [("book-a", FEED), ("book-b", CDN)])

    def test_search_image_microdata_ignores_decorative_images(self):
        first = card(FEED, extra='<img src="https://evil.invalid/avatar.jpg">')
        record, = parse_search_page(search(first)).records
        self.assertEqual(record.thumbnail_url, FEED)

    def test_detail_declared_book_cover_is_not_derived_from_related_link(self):
        # A recommendation nested in the same book container must not replace
        # an image that belongs to the detail page's exact resource.
        rec = ('<a href="/resources/other-book">'
               f'<img class="cover" src="{FEED_B}"></a>')
        html = detail(FEED, extra=rec)
        record, = parse_detail(html, page_url=ORIGIN + "/resources/book-a").records
        self.assertEqual(record.thumbnail_url, FEED)
        no_own_cover = (
            '<article itemscope itemtype="https://schema.org/Book">'
            '<h1 itemprop="name">Livre synthétique</h1>' + rec + '</article>'
        )
        record, = parse_detail(no_own_cover, page_url=ORIGIN + "/resources/book-a").records
        self.assertIsNone(record.thumbnail_url)

    def test_conflicting_declared_covers_and_hostile_single_cover_are_unknown(self):
        conflict = card(FEED, extra=f'<img class="cover" src="{FEED_B}">')
        self.assertIsNone(parse_search_page(search(conflict)).records[0].thumbnail_url)
        duplicate = parse_search_page(search(card(), card(FEED_B)))
        self.assertEqual(duplicate.status, "partial")
        self.assertIsNone(duplicate.records[0].thumbnail_url)
        hostile = FEED + "&token=synthetic-secret"
        self.assertIsNone(parse_detail(detail(hostile),
            page_url=ORIGIN + "/resources/book-a").records[0].thumbnail_url)

    def test_loan_activity_exact_resource_has_feedbooks_cover_without_rights_grant(self):
        scope = ProfileScope("alice", "account-a", "epoch-a")
        result = parse_loans(activity(), page_url=ORIGIN + "/my_profile/activity",
                             expected=scope, observed=scope)
        loan, = result.loans
        self.assertEqual(loan.thumbnail_url, FEED)
        self.assertFalse(result.exhaustive)
        self.assertIsNone(parse_loans(
            activity(FEED + "&token=secret"), page_url=ORIGIN + "/my_profile/activity",
            expected=scope, observed=scope,
        ).loans[0].thumbnail_url)

    def test_provider_search_and_holdings_propagate_exact_url_without_cover_get(self):
        calls = []

        class Client:
            def request(self, method, url, **kwargs):
                calls.append((method, url))
                if method != "GET":
                    raise AssertionError("unexpected write")
                if "/resources?" in url:
                    return url, search(card())
                if url == ORIGIN + "/resources/book-a":
                    return url, detail()
                raise AssertionError("unexpected GET")
            def clear(self):
                pass
            def close(self):
                pass

        session = BnfProviderSession(
            client=Client(), credential_id="credential-synthetic",
            label="BnF", credentials={}, options={})
        try:
            row, = session.search("fiction")
            self.assertEqual(row.thumbnail_url, FEED)
            self.assertFalse(row.downloadable)
            self.assertIsNone(row.owned)
            item = session.holdings(row)
            self.assertEqual(item.thumbnail_url, FEED)
            self.assertEqual(calls, [
                ("GET", ORIGIN + "/resources?q=fiction"),
                ("GET", ORIGIN + "/resources/book-a"),
            ])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
