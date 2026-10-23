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
        before = ORIGIN + "/results?q=fiction&availability=available&sort_by=title"
        observed = "/results?sort_by=title&availability=available&q=fiction&page=2"
        self.assertEqual(observed_next_results_url(before, observed), ORIGIN + observed)
        self.assertEqual(
            observed_next_results_url(ORIGIN + "/results?q=fiction&page=2",
                                      "?page=3&q=fiction"),
            ORIGIN + "/results?page=3&q=fiction",
        )

    def test_no_href_no_guessed_next(self):
        for observed in (None, "", 5, True, "page=2", "#next", " "):
            with self.subTest(observed=repr(observed)), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(ORIGIN + "/results?q=x", observed)

    def test_cross_origin_private_or_mutation_paths_denied(self):
        start = ORIGIN + "/results?q=x"
        for observed in (
            "https://evil.invalid/results?q=x&page=2",
            "https://pret.bnf.fr.evil.invalid/results?q=x&page=2",
            "https://pret.bnf.fr@evil.invalid/results?q=x&page=2",
            "http://pret.bnf.fr/results?q=x&page=2",
            "/loans/private?page=2", "/my_profile/activity?page=2",
            "/v1/results?q=x&page=2",
            "/results?q=x&page=2#private",
            "/results?q=x&page=2&token=SAML",
            "/results?q=x&page=2&q=secret",
            "/results?q=changed&page=2",
            "/results?q=x&page=2&sort_by=changed",
            "/results?q=x&page=2%0D%0Asecret",
            "/results?q=x&page=2\\evil",
            "/results?q=x&page=3",
            "/results?q=x&page=0",
            "/results?q=x&page=101",
            "/results?q=x&page=two",
        ):
            with self.subTest(observed=observed), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(start, observed)

    def test_current_page_must_be_current_public_results_route(self):
        target = "/results?q=x&page=2"
        for before in ("/results?q=x", ORIGIN + "/",
                       ORIGIN + "/resources/book",
                       ORIGIN + "/results?q=x&page=100",
                       ORIGIN + "/results?q=x&sig=private",
                       ORIGIN + "/results?q=x&page=2&page=3",
                       ORIGIN + "/results?q=x&page=abc",
                       ORIGIN + "/results?q=x%0Asecret"):
            with self.subTest(before=before), self.assertRaisesRegex(
                    WebParseError, "^next_page_unavailable$"):
                observed_next_results_url(before, target)

    def test_page_100_has_no_next_even_when_anchor_claims_one(self):
        with self.assertRaisesRegex(WebParseError, "^next_page_unavailable$"):
            observed_next_results_url(ORIGIN + "/results?page=100",
                                      "/results?page=101")


if __name__ == "__main__":
    unittest.main()
