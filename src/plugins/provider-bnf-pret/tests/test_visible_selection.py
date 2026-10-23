"""Only an observed visible link may navigate in the selected session."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_web import CatalogPage, CatalogRecord, ProfileScope, WebParseError
    from cifereca_bnf_pret_selection import VisibleResourceSelection
finally:
    sys.path.pop(0)


class BnfVisibleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.scope = ProfileScope("identity-a", "account-a", "epoch-a")
        self.chooser = VisibleResourceSelection(self.scope)

    def page(self, *ids, status="partial", scope="suggestions", exhaustive=False):
        return CatalogPage(tuple(CatalogRecord(item, f"Title {index}")
                                 for index, item in enumerate(ids)),
                           status, scope, exhaustive)

    def test_only_actual_observed_ids_not_guessed_from_title(self):
        self.assertEqual(self.chooser.observe(self.page("id_a", "id_b"), expected=self.scope), 2)
        revision = self.chooser.revision
        self.assertEqual(self.chooser.choose(0, expected=self.scope, revision=revision), "id_a")
        self.assertEqual(self.chooser.choose(1, expected=self.scope, revision=revision), "id_b")
        for index in (-1, 2, True, "0"):
            with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
                self.chooser.choose(index, expected=self.scope, revision=revision)

    def test_pages_are_bounded_to_displayed_first_twelve(self):
        ids = [f"id-{n}" for n in range(30)]
        self.assertEqual(self.chooser.observe(self.page(*ids), expected=self.scope), 12)
        self.assertEqual(self.chooser.choose(11, expected=self.scope,
                                          revision=self.chooser.revision), "id-11")
        with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
            self.chooser.choose(12, expected=self.scope, revision=self.chooser.revision)

    def test_new_page_revokes_previous_selection(self):
        self.chooser.observe(self.page("old"), expected=self.scope)
        revision = self.chooser.revision
        self.chooser.observe(self.page("new"), expected=self.scope)
        with self.assertRaisesRegex(WebParseError, "^selection_expired$"):
            self.chooser.choose(0, expected=self.scope, revision=revision)
        self.assertEqual(self.chooser.choose(0, expected=self.scope,
                                           revision=self.chooser.revision), "new")
        self.chooser.clear()
        with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
            self.chooser.choose(0, expected=self.scope, revision=self.chooser.revision)

    def test_scoped_to_exact_identity_account_and_session(self):
        self.chooser.observe(self.page("item"), expected=self.scope)
        other_scopes = (ProfileScope("other", "account-a", "epoch-a"),
                        ProfileScope("identity-a", "other", "epoch-a"),
                        ProfileScope("identity-a", "account-a", "other"))
        for other in other_scopes:
            with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
                self.chooser.choose(0, expected=other, revision=self.chooser.revision)

    def test_untrusted_routes_and_duplicates_are_not_selectable(self):
        for bad in ("/resources/item", "../loans/item", "id?token=private", "", "a/b", "id#frag",
                    "https://pret.bnf.fr/resources/item"):
            with self.assertRaisesRegex(WebParseError, "^invalid_resource$"):
                self.chooser.observe(self.page(bad), expected=self.scope)
            with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
                self.chooser.choose(0, expected=self.scope, revision=self.chooser.revision)
        with self.assertRaisesRegex(WebParseError, "^ambiguous_resource$"):
            self.chooser.observe(self.page("same", "same"), expected=self.scope)

    def test_failed_refresh_clears_prior_choices(self):
        self.chooser.observe(self.page("first"), expected=self.scope)
        with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
            self.chooser.observe(self.page("second", scope="detail"), expected=self.scope)
        with self.assertRaisesRegex(WebParseError, "^invalid_selection$"):
            self.chooser.choose(0, expected=self.scope, revision=self.chooser.revision)
        for page in (self.page(status="unverified"),
                     self.page("item", exhaustive=True),
                     self.page(*(f"id-{n}" for n in range(101)))):
            with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
                self.chooser.observe(page, expected=self.scope)

    def test_close_drops_all_ids_without_leaking_in_repr(self):
        self.chooser.observe(self.page("private-resource"), expected=self.scope)
        self.chooser.close()
        self.chooser.close()
        self.assertNotIn("identity-a", repr(self.chooser))
        self.assertNotIn("private-resource", repr(self.chooser))
        with self.assertRaisesRegex(WebParseError, "^session_closed$"):
            self.chooser.choose(0, expected=self.scope, revision=self.chooser.revision)
        with self.assertRaisesRegex(WebParseError, "^session_closed$"):
            self.chooser.observe(self.page("new"), expected=self.scope)


if __name__ == "__main__":
    unittest.main()
