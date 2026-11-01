"""Synthetic catalogue pagination; never contact a provider."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_paging import observed_next_results_url
    from cifereca_bnf_pret_web import WebParseError, ORIGIN
finally:
    sys.path.pop(0)


class BnfPagingContractTests(unittest.TestCase):
    def test_visible_next_page_preserves_query_and_filters(self):
        before = ORIGIN + "/resources?q=fiction&availability=available&sort_by=title"
        observed = "/resources?sort_by=title&availability=available&q=fiction&page=2"
        self.assertEqual(observed_next_results_url(before, observed), ORIGIN + observed)
        self.assertEqual(
            observed_next_results_url(ORIGIN + "/resources?q=fiction&page=2",
                                      "?page=3&q=fiction"),
            ORIGIN + "/resources?page=3&q=fiction",
        )

    def test_no_href_no_guessed_next(self):
        for observed in (None, "", 5, True, "page=2", "#next", " "):
            with self.subTest(observed=repr(observed)), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(ORIGIN + "/resources?q=x", observed)

    def test_cross_origin_private_or_mutation_paths_denied(self):
        start = ORIGIN + "/resources?q=x"
        for observed in (
            "https://evil.invalid/resources?q=x&page=2",
            "https://pret.bnf.fr.evil.invalid/resources?q=x&page=2",
            "https://pret.bnf.fr@evil.invalid/resources?q=x&page=2",
            "http://pret.bnf.fr/resources?q=x&page=2",
            "/loans/private?page=2", "/my_profile/activity?page=2",
            "/v1/resources?q=x&page=2",
            "/resources?q=x&page=2#private",
            "/resources?q=x&page=2&token=SAML",
            "/results?q=x&page=2",
            "/resources?q=x&page=2&q=secret",
            "/resources?q=changed&page=2",
            "/resources?q=x&page=2&sort_by=changed",
            "/resources?q=x&page=2%0D%0Asecret",
            "/resources?q=x&page=2\\evil",
            "/resources?q=x&page=3",
            "/resources?q=x&page=0",
            "/resources?q=x&page=101",
            "/resources?q=x&page=two",
        ):
            with self.subTest(observed=observed), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(start, observed)

    def test_current_page_must_be_current_public_search_route(self):
        target = "/resources?q=x&page=2"
        for before in ("/resources?q=x", ORIGIN + "/",
                       ORIGIN + "/results?q=x",
                       ORIGIN + "/resources/book",
                       ORIGIN + "/resources?q=x&page=100",
                       ORIGIN + "/resources?q=x&sig=private",
                       ORIGIN + "/resources?q=x&page=2&page=3",
                       ORIGIN + "/resources?q=x&page=abc",
                       ORIGIN + "/resources?q=x%0Asecret"):
            with self.subTest(before=before), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(before, target)

    def test_page_100_has_no_next_even_when_anchor_claims_one(self):
        with self.assertRaisesRegex(WebParseError, "^next_page_unavailable$"):
            observed_next_results_url(ORIGIN + "/resources?page=100",
                                      "/resources?page=101")


if __name__ == "__main__":
    unittest.main()
