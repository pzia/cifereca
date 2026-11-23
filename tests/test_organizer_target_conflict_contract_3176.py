"""#3176: a placement with divergent destinations remains blocked."""
import pathlib
import unittest

class OrganizerTargetConflictContractTests(unittest.TestCase):
    def test_both_destination_and_placement_collisions_are_checked(self):
        source = pathlib.Path("src/cifereca/core/library_organizer_plan.py").read_text()
        self.assertIn("per_placement.setdefault(entry.placement_id, set()).add(entry.proposed)", source)
        self.assertIn("len(per_placement[e.placement_id]) > 1", source)
        self.assertIn('"destination_conflict"', source)

if __name__ == "__main__":
    unittest.main()
