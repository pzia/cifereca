"""Read-only selection from a visible loan page, no library rights inferred."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_web import ProfileScope, LoanRecord, LoanSnapshot, WebParseError
    from cifereca_bnf_pret_loan_selection import VisibleLoanBookSelection
finally:
    sys.path.pop(0)


class BnfVisibleLoanBookSelectionTests(unittest.TestCase):
    def setUp(self):
        self.scope = ProfileScope("identity-private", "account-private", "epoch-private")
        self.chooser = VisibleLoanBookSelection(self.scope)

    def page(self, *loans, scope=None, status="observed", exhaustive=False):
        return LoanSnapshot(scope or self.scope, tuple(LoanRecord(a,b) for a,b in loans),
                            status, exhaustive)

    def test_only_public_book_route_from_observed_loan_not_loan_download_id(self):
        snapshot = self.page(("secret-loan1", "public-book1"),
                             ("secret-loan2", "public-book2"))
        self.assertEqual(self.chooser.observe(snapshot, expected=self.scope), 2)
        revision = self.chooser.revision
        self.assertEqual(self.chooser.choose(0, expected=self.scope, revision=revision),
                         "public-book1")
        self.assertEqual(self.chooser.choose(1, expected=self.scope, revision=revision),
                         "public-book2")
        self.assertNotIn("secret-loan", repr(self.chooser))
        self.assertNotIn("account-private", repr(self.chooser))
        self.assertEqual(self.chooser.count, 2)
        self.assertEqual(self.chooser.visible_book_lines(), (
            "1. Livre BnF [public-book1]",
            "2. Livre BnF [public-book2]",
        ))
        self.assertNotIn("secret-loan", "\n".join(self.chooser.visible_book_lines()))

    def test_empty_page_revokes_choices_without_claiming_return(self):
        self.chooser.observe(self.page(("loan1", "book1")), expected=self.scope)
        old = self.chooser.revision
        self.assertEqual(self.chooser.observe(self.page(status="partial"),
                                              expected=self.scope), 0)
        self.assertEqual(self.chooser.count, 0)
        with self.assertRaisesRegex(WebParseError, "^selection_expired$"):
            self.chooser.choose(0, expected=self.scope, revision=old)
        with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
            self.chooser.choose(0, expected=self.scope, revision=self.chooser.revision)

    def test_no_cross_identity_account_or_epoch(self):
        self.chooser.observe(self.page(("loan", "book")), expected=self.scope)
        for other in (ProfileScope("other", "account-private", "epoch-private"),
                      ProfileScope("identity-private", "other", "epoch-private"),
                      ProfileScope("identity-private", "account-private", "other")):
            with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
                self.chooser.choose(0, expected=other, revision=self.chooser.revision)
            with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
                self.chooser.observe(self.page(("loan", "book")), expected=other)
        self.assertEqual(self.chooser.count, 0)

    def test_validate_full_snapshot_even_after_twelve_visible_records(self):
        items = [(f"loan{i}", f"book{i}") for i in range(13)]
        self.assertEqual(self.chooser.observe(self.page(*items),
                                              expected=self.scope), 12)
        with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
            self.chooser.choose(12, expected=self.scope, revision=self.chooser.revision)
        with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
            self.chooser.observe(self.page(*items, ("loan13", "book/unsafe")),
                                 expected=self.scope)
        self.assertEqual(self.chooser.count, 0)

    def test_rejects_duplicate_loan_or_hostile_ids(self):
        for sample in (self.page(("id", "book"), ("id", "other")),
                       self.page(("secret?query", "book")),
                       self.page(("loan", "../loans/secret")),
                       self.page(("loan", "https://pret.bnf.fr/resources/book"))):
            with self.assertRaises(WebParseError):
                self.chooser.observe(sample, expected=self.scope)
            self.assertEqual(self.chooser.count, 0)

    def test_never_treats_partial_snapshot_as_exhaustive_inventory(self):
        self.assertEqual(self.chooser.observe(
            self.page(("loan", "book"), status="partial"), expected=self.scope), 1)
        for invalid in (self.page(exhaustive=True), self.page(status="complete"),
                        self.page(("loan", "book"),
                                  scope=ProfileScope("other", "other", "other"))):
            with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
                self.chooser.observe(invalid, expected=self.scope)

    def test_close_and_stale_selection_fail_closed(self):
        self.chooser.observe(self.page(("loan", "book")), expected=self.scope)
        revision = self.chooser.revision
        for index in (-1, 1, True, "0"):
            with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
                self.chooser.choose(index, expected=self.scope, revision=revision)
        self.chooser.clear()
        with self.assertRaisesRegex(WebParseError, "^selection_expired$"):
            self.chooser.choose(0, expected=self.scope, revision=revision)
        self.chooser.close()
        self.chooser.close()
        self.assertEqual(self.chooser.count, 0)
        with self.assertRaisesRegex(WebParseError, "^session_closed$"):
            self.chooser.observe(self.page(("loan", "book")), expected=self.scope)


if __name__ == "__main__":
    unittest.main()
