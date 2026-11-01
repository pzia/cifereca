"""P1 #4118: per-resource catalogue covers, no image GET or inferred ownership.

All HTML and identifiers are synthetic. A real BnF page must still qualify
the exact image-bearing DOM selectors before this issue can be closed.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))

from cifereca_bnf_pret_web import ORIGIN, parse_suggestions, parse_search_page, parse_detail
from cifereca_bnf_pret_catalog import read_search
from cifereca_bnf_pret import BnfProviderSession

COVER_A = "/covers/book-a.jpg"
COVER_B = "/covers/book-b.jpg"
CDN = "https://imagedelivery.net/QDkyDSqaJI1JEO0MqH_3SQ/d721a0b0-1425-45f0-6e08-aec9b2755500/default"


def suggestion(image=COVER_A, rid="book-a"):
    return ('<li class="search-resource-result"><a href="/resources/' + rid
            + '">Fiction<img data-src="' + image + '"></a></li>')


def full_card(image=COVER_A, rid="book-a", extra=""):
    return ('<li class="list-details__item"><article class="view-details" '
            'itemscope itemtype="http://schema.org/Book">'
            '<a href="/resources/' + rid + '"><h3 class="details-content__title">'
            'Fiction</h3></a><img src="' + image + '">' + extra
            + '</article></li>')


def results(*cards):
    return '<ul class="list-details">' + ''.join(cards) + '</ul>'


def detail(image=COVER_A, extra=""):
    return ('<html><head><meta property="og:image" content="/covers/unrelated.jpg"></head>'
            '<body><img src="/covers/unrelated.jpg">'
            '<article itemscope itemtype="https://schema.org/Book">'
            '<h1 itemprop="name">Fiction</h1><img data-src="' + image
            + '"><span itemprop="author" itemscope itemtype="https://schema.org/Person">'
            '<img src="/covers/avatar.jpg"><span itemprop="name">Auteur</span>'
            '</span>' + extra + '</article></body></html>')


class CatalogCoversTests(unittest.TestCase):
    def test_exact_suggestion_resource_link_only(self):
        page = parse_suggestions('<ul class="search-results__list">'
            + suggestion() + '<img src="/covers/decoy.jpg"></ul>')
        self.assertEqual(page.records[0].thumbnail_url, ORIGIN + COVER_A)
        self.assertIsNone(parse_suggestions('<ul class="search-results__list">'
            '<li class="search-resource-result"><a href="/resources/book-a">'
            'Fiction</a><img src="/covers/decoy.jpg"></li></ul>').records[0].thumbnail_url)

    def test_full_results_bound_to_each_book_not_neighbor_or_avatar(self):
        person = ('<span itemprop="author" itemscope itemtype="http://schema.org/Person">'
                  '<img src="/covers/avatar.jpg"></span>')
        parsed = parse_search_page(results(
            full_card(COVER_A, "book-a", person),
            full_card(CDN, "book-b")))
        self.assertEqual(parsed.status, "ok")
        self.assertEqual([x.thumbnail_url for x in parsed.records],
                         [ORIGIN + COVER_A, CDN])
        self.assertEqual([x.resource_id for x in parsed.records],
                         ["book-a", "book-b"])

    def test_detail_ignores_global_image_and_nested_person(self):
        record, = parse_detail(detail(), page_url=ORIGIN + "/resources/book-a").records
        self.assertEqual(record.thumbnail_url, ORIGIN + COVER_A)
        only_global = ('<html><meta property="og:image" content="' + COVER_A
                       + '"><article itemscope itemtype="https://schema.org/Book">'
                       '<h1 itemprop="name">Fiction</h1></article></html>')
        record, = parse_detail(only_global, page_url=ORIGIN + "/resources/book-a").records
        self.assertIsNone(record.thumbnail_url)

    def test_detail_accepts_unique_book_image_microdata(self):
        html = ('<article itemscope itemtype="https://schema.org/Book">'
                '<h1 itemprop="name">Fiction</h1>'
                '<meta itemprop="image" content="' + CDN + '"></article>')
        record, = parse_detail(html, page_url=ORIGIN + "/resources/book-a").records
        self.assertEqual(record.thumbnail_url, CDN)

    def test_conflicts_and_unsafe_images_are_unknown(self):
        unsafe = ("https://evil.test/covers/book-a.jpg",
                  "https://pret.bnf.fr/covers/book-a.jpg?token=private",
                  "//evil.test/covers/a.jpg", "data:image/png;base64,AAAA",
                  "/sso/private.jpg", "/covers/../covers/book-a.jpg")
        for image in unsafe:
            with self.subTest(image=image):
                page = parse_search_page(results(full_card(image)))
                self.assertIsNone(page.records[0].thumbnail_url)
                self.assertIsNone(parse_detail(detail(image),
                    page_url=ORIGIN + "/resources/book-a").records[0].thumbnail_url)
        conflict = parse_search_page(results(
            full_card(COVER_A, extra='<img src="' + COVER_B + '">')))
        self.assertIsNone(conflict.records[0].thumbnail_url)
        duplicate = parse_search_page(results(full_card(COVER_A) + full_card(COVER_B)))
        self.assertEqual(duplicate.status, "partial")
        self.assertIsNone(duplicate.records[0].thumbnail_url)
        repeated = parse_search_page(results(
            full_card(COVER_A) + full_card(COVER_B) + full_card(COVER_A)))
        self.assertIsNone(repeated.records[0].thumbnail_url)

    def test_paginated_duplicate_cover_conflict_does_not_choose_first(self):
        class Client:
            def request(self, method, url, **kwargs):
                assert method == "GET"
                if "page=2" in url:
                    return url, results(full_card(COVER_B))
                first = (results(full_card(COVER_A)) +
                         '<nav class="pagination"><a rel="next" '
                         'href="/resources?q=fiction&page=2">Suivant</a></nav>')
                return url, first
        records, warnings = read_search(Client(), "fiction")
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].thumbnail_url)
        self.assertIn("ambiguous_search", warnings)

    def test_provider_search_and_detail_project_without_image_get(self):
        calls = []
        class Client:
            def request(self, method, url, **kwargs):
                calls.append((method, url))
                assert method == "GET"
                if "/resources?" in url:
                    return url, results(full_card(COVER_A))
                if url == ORIGIN + "/resources/book-a":
                    return url, detail(CDN)
                raise AssertionError("Unexpected network request")
            def close(self):
                pass
        session = BnfProviderSession(
            client=Client(), credential_id="credential-synthetic",
            label="BnF", credentials={}, options={})
        try:
            row, = session.search("fiction")
            self.assertEqual(row.thumbnail_url, ORIGIN + COVER_A)
            self.assertFalse(row.downloadable)
            self.assertIsNone(row.owned)
            item = session.holdings(row)
            self.assertEqual(item.thumbnail_url, CDN)
            self.assertEqual(item.record_id, "book-a")
            self.assertEqual([url for _, url in calls],
                [ORIGIN + "/resources?q=fiction", ORIGIN + "/resources/book-a"])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
