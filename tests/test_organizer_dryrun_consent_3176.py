"""#3176: the organizer cannot construct a mutation plan from invalid consent."""
import unittest
from cifereca.core.library_organizer_plan import prepare_organizer_dry_run

class OrganizerDryRunConsentTests(unittest.TestCase):
    def test_no_scope_or_report_never_prepares_a_plan(self):
        for confirmed in (False, True, 1, None):
            with self.subTest(confirmed=confirmed):
                self.assertIsNone(prepare_organizer_dry_run(None, {}, confirmed=confirmed))

    def test_invalid_group_selection_never_prepares_a_plan(self):
        for index in (True, -1, 4, "0"):
            with self.subTest(index=index):
                self.assertIsNone(prepare_organizer_dry_run(None, {}, confirmed=True, group_index=index))

    def test_invalid_rename_only_flag_never_prepares_a_plan(self):
        self.assertIsNone(prepare_organizer_dry_run(None, {}, confirmed=True, rename_only=1))

if __name__ == "__main__":
    unittest.main()
