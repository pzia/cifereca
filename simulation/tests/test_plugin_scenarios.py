from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src/plugins/literary-criticism",
             REPO / "src/plugins/constellations"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import plugin_coverage
import plugin_scenarios


class PluginCoverageTests(unittest.TestCase):
    def test_inventory_matches_all_current_manifests(self):
        result = plugin_coverage.build_report()
        self.assertEqual(result["unclassified"], [], "Tout nouveau plugin doit être audité")
        self.assertEqual(result["stale"], [])
        self.assertEqual(result["stats"]["packages"], len(list((REPO / "src/plugins").glob("*/cifereca-plugin.json"))))
        self.assertEqual(len({row["manifest_id"] for row in result["packages"]}), result["stats"]["packages"])
        self.assertFalse(any(row["e2e_gtk_verified"] for row in result["packages"]))
        self.assertIn("baen", [row["package"] for row in result["packages"] if not row["data"]])

    def test_pilot_plugins_classified_without_claiming_real_e2e(self):
        report = plugin_coverage.build_report()
        packages = {row["package"]: row for row in report["packages"]}
        for name in ("dev-updater",):
            self.assertIn(name, packages)
            row = packages[name]
            self.assertEqual(row["data"], [])
            self.assertEqual(row["services"], [])
            self.assertFalse(row["e2e_gtk_verified"])
            self.assertTrue(row["gap"])
        self.assertNotIn("dev-updater", report["unclassified"])

    def test_all_services_profiles_include_ebooksgratuits(self):
        import datasetctl
        catalog = datasetctl.load_catalog()
        for profile_name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(profile_name)
            self.assertIn("ebooksgratuits", datasetctl.enabled_service_configs(profile))
            self.assertIn("provider.ebooksgratuits", datasetctl.derived_plugins(profile, catalog))

    def test_services_are_derived_from_manifests_not_invented(self):
        rows = {row["package"]: row for row in plugin_coverage.build_report()["packages"]}
        self.assertIn("kobo", rows["provider-kobo"]["services"])
        self.assertEqual(rows["constellations"]["services"], [])
        self.assertNotIn("youth-catalog", rows)
        self.assertIn("browser-recorder", rows)


class SpecializedFixturesTests(unittest.TestCase):
    def test_generation_is_reproducible_and_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            first = plugin_scenarios.create(a)
            second = plugin_scenarios.create(b)
            self.assertEqual(first, second)
            self.assertTrue(first["synthetic"])
            self.assertIn("optimizer-valid.epub", first["files"])
            with zipfile.ZipFile(a / "optimizer-valid.epub") as archive:
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            with self.assertRaises(zipfile.BadZipFile):
                with zipfile.ZipFile(a / "optimizer-corrupt.epub") as archive:
                    archive.namelist()
            youth = json.loads((a / "youth.json").read_text(encoding="utf-8"))
            self.assertIn("age_boundary_18_no_match", youth["cases"])
            self.assertTrue(any(not record["age_ranges"] for record in youth["records"]))
            self.assertEqual(len(first["files"]), 10)
            self.assertEqual(first["version"], plugin_scenarios.SCHEMA_VERSION)

    def test_archived_youth_fixture_remains_available_without_plugin(self):
        rows = plugin_scenarios.youth_records()
        self.assertTrue(rows["records"])
        self.assertIn("age_boundary_18_no_match", rows["cases"])

    def test_criticism_feed_is_parseable_by_plugin(self):
        from criticism_feeds import parse_feed
        rss = parse_feed(plugin_scenarios.criticism_rss().encode("utf-8"),
                         "https://ux-fixtures.invalid/reviews.rss")
        self.assertEqual(len(rss), 2)
        self.assertEqual(rss[0]["book"]["isbns"], [plugin_scenarios.ISBN_A])
        self.assertEqual(rss[1]["book"]["isbns"], [])
        feed = json.dumps(plugin_scenarios.criticism_jsonfeed()).encode("utf-8")
        parsed = parse_feed(feed, "https://ux-fixtures.invalid/reviews.json")
        self.assertEqual(parsed[0]["book"]["isbns"], [plugin_scenarios.ISBN_A])

    def test_constellation_data_builds_connected_and_disconnected_nodes(self):
        from constellations import ConstellationGraph
        rows = plugin_scenarios.constellations_records()["records"]
        graph = ConstellationGraph(rows)
        self.assertEqual(len(graph.books), len(rows))
        linked = [book for book in graph.books if graph.neighbors(book)]
        self.assertGreaterEqual(len(linked), 6)


if __name__ == "__main__":
    unittest.main()
