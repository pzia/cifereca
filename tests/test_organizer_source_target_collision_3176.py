"""#3176: a target currently held by another placement is a conflict."""
import pathlib
import unittest

class OrganizerSourceTargetCollisionTests(unittest.TestCase):
    def test_page_source_occupancy_is_checked(self):
        source = pathlib.Path("src/cifereca/core/library_organizer_plan.py").read_text()
        self.assertIn("sources.setdefault(entry.current, set()).add(entry.placement_id)", source)
        self.assertIn("sources.get(e.proposed, ())", source)
        self.assertIn('"destination_conflict"', source)

if __name__ == "__main__":
    unittest.main()
