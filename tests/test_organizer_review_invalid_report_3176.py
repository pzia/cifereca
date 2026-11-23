"""#3176: malformed organizer reports never create an executable plan."""
import unittest
from cifereca.core.library_organizer_plan import prepare_organizer_dry_run

class OrganizerInvalidReportTests(unittest.TestCase):
    def test_missing_scope_or_report_is_rejected(self):
        for report in (None, {}, [], {"mutation_authorized": True},
                       {"review_anchor": None}, {"groups": []}):
            with self.subTest(report=repr(report)):
                self.assertIsNone(prepare_organizer_dry_run(None, report, confirmed=True))

    def test_confirmation_is_exact_boolean(self):
        for confirmed in (0, 1, "yes", None):
            with self.subTest(confirmed=confirmed):
                self.assertIsNone(prepare_organizer_dry_run(None, {}, confirmed=confirmed))

if __name__ == "__main__":
    unittest.main()
