"""#3176: held-only advice cannot be confirmed as an actionable dry-run."""
import unittest
from cifereca.core.library_organizer_plan import (
    OrganizerDryRunEntry, OrganizerDryRunPlan, confirm_organizer_dry_run,
)

class OrganizerActionableConfirmationTests(unittest.TestCase):
    def test_held_only_plan_cannot_be_confirmed(self):
        plan = OrganizerDryRunPlan(
            scope=None, anchor=None,
            entries=(OrganizerDryRunEntry("p", "keeper", "/a", "/b", "cleanup_held"),),
        )
        self.assertIsNone(confirm_organizer_dry_run(None, {}, plan, confirmed=True))

    def test_conflict_only_plan_cannot_be_confirmed(self):
        plan = OrganizerDryRunPlan(
            scope=None, anchor=None,
            entries=(OrganizerDryRunEntry("p", "rename", "/a", "/b", "destination_conflict"),),
        )
        self.assertIsNone(confirm_organizer_dry_run(None, {}, plan, confirmed=True))

if __name__ == "__main__":
    unittest.main()
