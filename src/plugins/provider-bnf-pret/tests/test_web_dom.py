"""Synthetic contracts only. No private capture, credentials or network."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bnf_web", ROOT / "cifereca_bnf_pret_web.py")
web = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = web
spec.loader.exec_module(web)


def suggestions(cards):
    return '<ul class="search-results__list">' + cards + '</ul>'


def card(rid="synthetic-book", title="Livre synthétique"):
    return f'<li class="search-resource-result"><a href="/resources/{rid}">{title}</a></li>'


def detail(body):
    return '<article itemscope itemtype="https://schema.org/Book">' + body + '</article>'


def loans(body):
    return '<section class="active-loans">' + body + '</section>'


def loan(lid="synthetic-loan", rid="synthetic-book", query=""):
    return (f'<div class="js-loan"><a href="/resources/{rid}">Livre fictif</a>'
            f'<a href="/loans/{lid}{query}">Télécharger</a></div>')


class WebDOMTests(unittest.TestCase):
    def setUp(self):
        self.scope = web.ProfileScope("identity-a", "account-a", "epoch-a")

    def parse_loans(self, html, **kwargs):
        params = dict(page_url=web.ORIGIN + "/my_profile/activity", expected=self.scope, observed=self.scope)
        params.update(kwargs)
        return web.parse_loans(html, **params)

    def parse_detail(self, html):
        return web.parse_detail(html, page_url=web.ORIGIN + "/resources/synthetic-book")

    def test_suggestion_counts(self):
        for count in (0, 1, 4, 12):
            with self.subTest(count=count):
                result = web.parse_suggestions(suggestions(''.join(card(str(i)) for i in range(count))))
                self.assertEqual(len(result.records), count)
                self.assertEqual(result.status, "ok")
                self.assertFalse(result.exhaustive)

    def test_homonyms_are_distinct_resources(self):
        page = web.parse_suggestions(suggestions(card("a") + card("b") + card("a")))
        self.assertEqual(len(page.records), 2)

    def test_conflicting_title_marks_partial(self):
        page = web.parse_suggestions(suggestions(card("a", "A") + card("a", "B")))
        self.assertEqual(page.status, "partial")

    def test_ambiguous_card_is_not_a_record(self):
        page = web.parse_suggestions(suggestions('<li class="search-resource-result">'
            '<a href="/resources/a">A</a><a href="/resources/b">B</a></li>'))
        self.assertEqual(page.records, ())
        self.assertEqual(page.status, "partial")

    def test_unrecognized_search_is_not_empty_success(self):
        for html in ('<html>Erreur temporaire</html>', '<ul class="search-results__list">' + card()):
            with self.assertRaises(web.WebParseError):
                web.parse_suggestions(html)

    def test_link_origin_and_routes(self):
        bad = ('https://evil.test/resources/a', '//evil.test/resources/a',
               'https://pret.bnf.fr@evil.test/resources/a', 'http://pret.bnf.fr/resources/a',
               'https://pret.bnf.fr:443/resources/a', '/resources/%2e%2e',
               '/resources/a/download', '/v1/resources/a', 'javascript:alert(1)',
               '/resources/a\\b', '/resources/a\nb')
        for href in bad:
            with self.subTest(href=href):
                self.assertIsNone(web._id(href, "resources"))
        self.assertEqual(web._id(web.ORIGIN + '/resources/a?secret=synthetic#frag', 'resources'), 'a')

    def test_detail_microdata_and_nested_person(self):
        html = detail('<h1 itemprop="name">Livre fictif</h1>'
            '<span itemprop="author" itemscope itemtype="https://schema.org/Person">'
            '<span itemprop="name">Auteur fictif</span></span>'
            '<meta itemprop="publisher" content="Éditeur fictif">'
            '<meta itemprop="isbn" content="9780000000002">'
            '<meta itemprop="inLanguage" content="fr">'
            '<div itemprop="description">Texte <b>lisible</b><script>secret()</script></div>')
        record = self.parse_detail(html).records[0]
        self.assertEqual(record.title, 'Livre fictif')
        self.assertEqual(record.authors, ('Auteur fictif',))
        self.assertEqual(record.publisher, 'Éditeur fictif')
        self.assertEqual(record.description, 'Texte lisible')
        self.assertEqual(record.availability, 'unknown')

    def test_button_does_not_prove_availability(self):
        for body, expected in (('<button class="button-borrow">Emprunter</button>', 'unknown'),
                ('<button class="button-borrow">Emprunter</button><p class="lb_unavailable">Indisponible</p>', 'unavailable'),
                ('<button class="button-booking">Réserver</button>', 'unknown')):
            page = self.parse_detail(detail('<h1 itemprop="name">Titre</h1>' + body))
            self.assertEqual(page.records[0].availability, expected)

    def test_missing_optional_data_remains_unknown(self):
        record = self.parse_detail(detail('<h1 itemprop="name">Titre</h1>')).records[0]
        self.assertIsNone(record.isbn)
        self.assertEqual(record.authors, ())

    def test_conflicting_single_value_is_omitted(self):
        result = self.parse_detail(detail('<h1 itemprop="name">Titre</h1>'
            '<meta itemprop="isbn" content="a"><meta itemprop="isbn" content="b">'))
        self.assertEqual(result.status, 'partial')
        self.assertIsNone(result.records[0].isbn)

    def test_detail_does_not_use_account_name(self):
        with self.assertRaises(web.WebParseError):
            self.parse_detail('<h1 itemprop="name">Compte fictif</h1>')
        with self.assertRaises(web.WebParseError):
            self.parse_detail(detail('<span itemprop="name">A</span><span itemprop="name">B</span>'))

    def test_two_distinct_loans(self):
        result = self.parse_loans(loans(loan("a", "ra") + loan("b", "rb")))
        self.assertEqual(len(result.loans), 2)
        self.assertEqual(result.status, 'observed')
        self.assertFalse(result.exhaustive)

    def test_signed_links_are_not_returned(self):
        result = self.parse_loans(loans(loan(query="?signature=synthetic-private-marker")))
        self.assertNotIn('synthetic-private-marker', repr(result))
        self.assertEqual(result.loans[0].loan_id, 'synthetic-loan')
        self.assertEqual(set(vars(result.loans[0])), {'loan_id', 'resource_id', 'title', 'authors', 'thumbnail_url'})
        self.assertEqual(result.loans[0].authors, ())

    def test_profile_account_and_session_isolation(self):
        for observed in (web.ProfileScope("b", "account-a", "epoch-a"),
                         web.ProfileScope("identity-a", "b", "epoch-a"),
                         web.ProfileScope("identity-a", "account-a", "b"), None):
            with self.assertRaisesRegex(web.WebParseError, '^scope_mismatch$'):
                self.parse_loans(loans(loan()), observed=observed)

    def test_no_scope_from_page_text(self):
        with self.assertRaises(web.WebParseError):
            self.parse_loans(loans(loan()), expected="identity-a", observed="identity-a")

    def test_authentication_page_refused(self):
        for form in ('<input name="SAMLResponse" value="synthetic-secret">',
                     '<input type="password">', '<input name="password">'):
            with self.assertRaisesRegex(web.WebParseError, '^auth_required$') as ctx:
                self.parse_loans(loans(loan()) + form)
            self.assertNotIn('synthetic-secret', str(ctx.exception))

    def test_wrong_activity_location_refused(self):
        for url in (web.ORIGIN + '/home', web.ORIGIN + '/my_profile/activity?account=b',
                    'https://evil.test/my_profile/activity'):
            with self.assertRaises(web.WebParseError):
                self.parse_loans(loans(loan()), page_url=url)

    def test_unknown_or_incomplete_loans_are_not_empty_success(self):
        for html in ('<html>Erreur</html>', '<section class="active-loans">' + loan()):
            with self.assertRaises(web.WebParseError):
                self.parse_loans(html)
        self.assertEqual(self.parse_loans(loans('')).status, 'partial')

    def test_malformed_card_marks_partial(self):
        result = self.parse_loans(loans(loan() + '<div class="js-loan">chargement</div>'))
        self.assertEqual(len(result.loans), 1)
        self.assertEqual(result.status, 'partial')

    def test_conflicting_loan_resource_quarantines_all_ambiguous_occurrences(self):
        snapshot = self.parse_loans(loans(loan('a', 'ra') + loan('a', 'rb')))
        self.assertEqual(snapshot.loans, ())
        self.assertEqual(snapshot.status, 'partial')
        self.assertFalse(snapshot.exhaustive)

    def test_conflicting_right_id_preserves_only_independent_positive_loans(self):
        snapshot = self.parse_loans(loans(
            loan('a', 'ra') + loan('safe', 'safe-edition')
            + loan('a', 'rb') + loan('a', 'ra')
            + loan('another', 'other-edition')
        ))
        self.assertEqual(
            [(row.loan_id, row.resource_id) for row in snapshot.loans],
            [('safe', 'safe-edition'), ('another', 'other-edition')],
        )
        self.assertEqual(snapshot.status, 'partial')
        self.assertFalse(snapshot.exhaustive)

    def test_absence_never_deletes_previous_observation(self):
        first = self.parse_loans(loans(loan('a', 'ra') + loan('b', 'rb')))
        empty = self.parse_loans(loans(''))
        merged = web.merge_observed_loans(first, empty)
        self.assertEqual(len(merged.loans), 2)
        self.assertEqual(merged.status, 'partial')
        self.assertFalse(merged.exhaustive)

    def test_merging_scopes_and_contradictions_rejected(self):
        first = self.parse_loans(loans(loan()))
        other = web.LoanSnapshot(web.ProfileScope('b', 'b', 'b'), (), 'partial')
        with self.assertRaises(web.WebParseError):
            web.merge_observed_loans(first, other)
        conflict = self.parse_loans(loans(loan(rid='other')))
        with self.assertRaises(web.WebParseError):
            web.merge_observed_loans(first, conflict)

    def test_html_node_depth_and_item_limits(self):
        for html, code in (("é" * web.MAX_HTML_BYTES, 'html_limit'),
                ('<div>' * 70, 'dom_limit'),
                ('<br>' * (web.MAX_NODES + 1), 'dom_limit'),
                (suggestions(card() * (web.MAX_ITEMS + 1)), 'item_limit')):
            with self.assertRaisesRegex(web.WebParseError, '^' + code + '$'):
                web.parse_suggestions(html)

    def test_hidden_markup_cannot_supply_records(self):
        page = web.parse_suggestions(suggestions('<div hidden>' + card() + '</div>'))
        self.assertEqual(page.records, ())

    def test_duplicate_attributes_refused(self):
        with self.assertRaisesRegex(web.WebParseError, '^invalid_dom$'):
            web.parse_suggestions('<ul class="x" class="search-results__list"></ul>')

    def test_loan_cover_requires_an_exact_resource_link(self):
        safe = '/images/covers/synthetic-book.jpg'
        card = ('<div class="js-loan">'
                '<a href="/resources/synthetic-book">Livre'
                '<img src="' + safe + '"></a>'
                '<a href="/loans/synthetic-loan">Télécharger</a></div>')
        result = self.parse_loans(loans(card))
        self.assertEqual(len(result.loans), 1)
        self.assertEqual(result.loans[0].thumbnail_url, web.ORIGIN + safe)
        self.assertFalse(result.exhaustive)
        # Neither an unrelated image nor another resource can supply a cover.
        unrelated = ('<img src="' + safe + '">' + loan())
        self.assertIsNone(self.parse_loans(loans(unrelated)).loans[0].thumbnail_url)
        wrong = ('<div class="js-loan">'
                 '<a href="/resources/synthetic-book">Titre</a>'
                 '<a href="/resources/other"><img src="' + safe + '"></a>'
                 '<a href="/loans/synthetic-loan">Télécharger</a></div>')
        self.assertEqual(self.parse_loans(loans(wrong)).loans, ())

    def test_loan_cover_valid_public_cdn_and_lazy_image(self):
        cdn = 'https://imagedelivery.net/abcdefghijk/abcdef1234567890/public'
        card = ('<div class="js-loan"><a href="/resources/book-a">'
                '<img data-src="' + cdn + '" src="data:image/gif;base64,AAAA">'
                'Livre</a><a href="/loans/loan-a">Jeton</a></div>')
        result = self.parse_loans(loans(card))
        self.assertEqual(result.loans[0].thumbnail_url, cdn)

    def test_loan_cover_accepts_real_public_cdn_default_variant(self):
        # Public URL structure recorded in #3104; all HTML here is synthetic.
        cdn = ('https://imagedelivery.net/QDkyDSqaJI1JEO0MqH_3SQ/'
               'd721a0b0-1425-45f0-6e08-aec9b2755500/default')
        card = ('<div class="js-loan"><a href="/resources/book-a">'
                '<img data-src="' + cdn + '" src="data:image/gif;base64,AAAA">'
                'Livre</a><a href="/loans/loan-a">Jeton</a></div>')
        row, = self.parse_loans(loans(card)).loans
        self.assertEqual(row.thumbnail_url, cdn)
        self.assertEqual(web._public_cover_url(cdn), cdn)
        self.assertIsNone(web._public_cover_url(cdn + '?token=synthetic'))
        self.assertIsNone(web._public_cover_url(cdn.replace('/default', '/private')))
        self.assertIsNone(
            self.parse_loans(loans('<img src="' + cdn + '">' + loan())).loans[0].thumbnail_url
        )

    def test_loan_cover_rejects_private_and_ambiguous_urls(self):
        bad = (
            'https://evil.test/images/covers/a.jpg',
            '//evil.test/images/covers/a.jpg',
            'http://pret.bnf.fr/images/covers/a.jpg',
            'https://pret.bnf.fr@evil.test/images/covers/a.jpg',
            'https://pret.bnf.fr/images/covers/a.jpg?token=private',
            'https://pret.bnf.fr/images/covers/a.jpg#fragment',
            'https://pret.bnf.fr/loans/a.jpg',
            'https://pret.bnf.fr/sso/saml/cover.jpg',
            'https://pret.bnf.fr/images/../cover.jpg',
            'https://pret.bnf.fr/images/../covers/cover.jpg',
            'https://pret.bnf.fr/images/%2e%2e/cover.jpg',
            'https://imagedelivery.net/abcdefghijk/abcdef1234567890/private',
            'data:image/png;base64,AAAA',
            'javascript:alert(1)',
        )
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(web._public_cover_url(url))
        safe = '/covers/a.jpg'
        card = ('<div class="js-loan"><a href="/resources/synthetic-book">'
                '<img src="' + safe + '">'
                '<img src="/covers/b.jpg"></a>'
                '<a href="/loans/synthetic-loan">Fichier</a></div>')
        self.assertIsNone(self.parse_loans(loans(card)).loans[0].thumbnail_url)

    def test_duplicate_loan_cover_conflict_is_partial_and_never_guessed(self):
        def image_card(url):
            return ('<div class="js-loan"><a href="/resources/book-a">'
                    '<img src="' + url + '">Livre</a>'
                    '<a href="/loans/loan-a">Jeton</a></div>')
        result = self.parse_loans(loans(
            image_card('/covers/a.jpg') + image_card('/covers/b.jpg')))
        self.assertEqual(len(result.loans), 1)
        self.assertIsNone(result.loans[0].thumbnail_url)
        self.assertEqual(result.status, 'partial')
        # A later repetition cannot restore the earlier, conflicting cover.
        result = self.parse_loans(loans(
            image_card('/covers/a.jpg') + image_card('/covers/b.jpg')
            + image_card('/covers/a.jpg')))
        self.assertIsNone(result.loans[0].thumbnail_url)

    def test_private_values_are_not_in_repr(self):
        record = web.CatalogRecord('private-id', 'private-title', ('private-author',))
        scope = web.ProfileScope('private-identity', 'private-account', 'private-epoch')
        for value in (record, scope, web.LoanRecord('private-loan', 'private-book')):
            self.assertNotIn('private-', repr(value))


if __name__ == '__main__':
    unittest.main()
