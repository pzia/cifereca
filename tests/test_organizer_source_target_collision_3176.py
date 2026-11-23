"""#3176: active and passive page placements reserve their current paths."""
import unittest

from cifereca.core.library_organizer_plan import (
    OrganizerDryRunEntry, _mark_conflicted_page_destinations,
)


class OrganizerSourceTargetCollisionTests(unittest.TestCase):
    def test_passive_placement_blocks_proposed_target(self):
        rows = (OrganizerDryRunEntry(
            "first", "rename", "/root/first.epub", "/root/passive.epub", "review_only"),)
        groups = ({"files": (
            {"placement": "first", "current": "/root/first.epub"},
            {"placement": "passive", "current": "/root/passive.epub"},
        )},)
        checked = _mark_conflicted_page_destinations(rows, groups)
        self.assertEqual(checked[0].state, "destination_conflict")

    def test_no_collision_keeps_advice_only(self):
        rows = (OrganizerDryRunEntry(
            "first", "rename", "/root/first.epub", "/root/free.epub", "review_only"),)
        groups = ({"files": (
            {"placement": "first", "current": "/root/first.epub"},
            {"placement": "passive", "current": "/root/passive.epub"},
        )},)
        self.assertEqual(_mark_conflicted_page_destinations(
            rows, groups)[0].state, "review_only")

    def test_divergent_advice_for_one_placement_is_blocked(self):
        rows = (
            OrganizerDryRunEntry("first", "rename", "/root/first.epub",
                                 "/root/a.epub", "review_only"),
            OrganizerDryRunEntry("first", "classify", "/root/first.epub",
                                 "/root/b.epub", "relocation_held"),
        )
        groups = ({"files": ({"placement": "first", "current": "/root/first.epub"},)},)
        checked = _mark_conflicted_page_destinations(rows, groups)
        self.assertTrue(all(e.state == "destination_conflict" for e in checked))


if __name__ == "__main__":
    unittest.main()
