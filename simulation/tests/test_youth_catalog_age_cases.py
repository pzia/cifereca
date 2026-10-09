"""#841: real YouthCatalogIndex against the deterministic youth editorial JDD.

Only cache operations execute: source collection is not started and no network
request is possible. The actual GTK page is covered separately by #758.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src", REPO / "src/plugins/youth-catalog"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import plugin_scenarios
from youth_catalog import YouthCatalogIndex


class YouthCatalogAgeCases(unittest.TestCase):
    def setUp(self):
        data = plugin_scenarios.youth_records()
        self.assertTrue(data["synthetic"])
        self.assertEqual(data["identities"], ["ux-camille", "ux-alexandre"])
        self.data = data
        self.index = YouthCatalogIndex()
        records = data["records"]
        # Same ISBN on two distinct attested sources; different ISBN on a
        # homonymous edition. Source and edition boundaries must both survive.
        primary = [row for row in records if row["source_item_id"] != "6"]
        secondary = [row for row in records if row["source_item_id"] == "6"]
        self.assertEqual(
            self.index.ingest("ux-camille", "ux-youth", primary).accepted, 6
        )
        self.assertEqual(
            self.index.ingest("ux-camille", "ux-youth-second", secondary).accepted, 1
        )
        self.assertEqual(
            self.index.ingest("ux-alexandre", "ux-youth", [records[1]]).accepted, 1
        )

    def names(self, identity="ux-camille", **filters):
        return {book.title for book in self.index.search(identity, **filters)}

    def test_ages_zero_eight_twelve_seventeen_and_adult_boundary(self):
        self.assertEqual(self.names(age=0), {"Le Jardin des mots"})
        self.assertEqual(self.names(age=8, age_kind="interest"), {"L'Atlas des lucioles"})
        self.assertEqual(self.names(age=8, age_kind="reading"), set())
        self.assertEqual(self.names(age=10, age_kind="reading"), {"L'Atlas des lucioles"})
        self.assertEqual(self.names(age=12, age_kind="reading"), {"L'Atlas des lucioles"})
        self.assertEqual(self.names(age=13, age_kind="publisher"), {"L'Atlas des lucioles"})
        self.assertEqual(self.names(age=17), {"Le Dernier Rivage"})
        self.assertEqual(self.names(age=18), set())
        self.assertEqual(self.names(age=8, include_inferred=True), {"L'Atlas des lucioles"})

    def test_unknown_and_inferred_age_are_distinct_and_opt_in(self):
        self.assertIn("Le Passage sans âge", self.names())
        self.assertNotIn("Le Passage sans âge", self.names(age=12))
        self.assertNotIn("La Carte supposée", self.names(age=12))
        self.assertIn("La Carte supposée", self.names(age=12, include_inferred=True))
        inferred = next(b for b in self.index.entries("ux-camille") if b.title == "La Carte supposée")
        self.assertEqual(inferred.age_ranges[0].age_kind, "inferred")
        self.assertEqual(inferred.age_ranges[0].confidence, 0.55)
        self.assertEqual(inferred.age_ranges[0].source_label, "inférence, NON attestée")

    def test_same_isbn_merges_provenance_but_homonymous_edition_does_not(self):
        rows = [b for b in self.index.entries("ux-camille") if b.title == "L'Atlas des lucioles"]
        self.assertEqual(len(rows), 2, "Different ISBN editions must not be collapsed")
        exact = next(b for b in rows if b.identifiers.get("isbn") == plugin_scenarios.ISBN_A)
        other = next(b for b in rows if b.identifiers.get("isbn") == plugin_scenarios.ISBN_C)
        self.assertNotEqual(exact.identifiers["isbn"], other.identifiers["isbn"])
        self.assertEqual(set(exact.provenance), {"ux-youth", "ux-youth-second"})
        self.assertEqual(set((a.source_id, a.source_label) for a in exact.age_ranges), {
            ("ux-youth", "Interest age 8-11"),
            ("ux-youth", "Reading age 10-12"),
            ("ux-youth-second", "6–7 ans"),
        })
        self.assertEqual(len(other.age_ranges), 1)
        self.assertEqual(other.age_ranges[0].source_label, "13–15 ans")
        self.assertEqual(len(self.index.entries("ux-camille")), 6)
        self.assertEqual(
            {b.identifiers.get("isbn") for b in self.index.search(
                "ux-camille", age=6, source_id="ux-youth-second"
            )}, {plugin_scenarios.ISBN_A}
        )

    def test_identities_are_isolated_even_after_source_refresh_and_reset(self):
        self.assertEqual(self.names("ux-alexandre"), {"Le Jardin des mots"})
        self.assertEqual(self.names("ux-alexandre", age=17), set())
        self.assertEqual(len(self.index.entries("ux-camille")), 6)
        self.assertEqual(self.index.ingest("ux-camille", "ux-youth", ()).accepted, 0)
        self.assertEqual(self.names("ux-camille"), {"L'Atlas des lucioles"})
        self.assertEqual(self.names("ux-alexandre"), {"Le Jardin des mots"})
        self.index.clear_identity("ux-camille")
        self.assertEqual(self.names("ux-camille"), set())
        self.assertEqual(self.names("ux-alexandre"), {"Le Jardin des mots"})

    def test_cache_replay_is_reproducible_and_has_no_external_calls(self):
        first = [(b.title, b.identifiers.get("isbn"), b.provenance)
                 for b in self.index.entries("ux-camille")]
        second = [(b.title, b.identifiers.get("isbn"), b.provenance)
                  for b in self.index.entries("ux-camille")]
        self.assertEqual(first, second)
        self.assertEqual(self.data["cases"]["age_boundary_18_no_match"], [])
        self.assertEqual(self.data["cases"]["empty_source"], [])


if __name__ == "__main__":
    unittest.main()
