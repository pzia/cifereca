"""#770 — audited source evidence, not fabricated simulation/E2E success."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

import plugin_coverage as coverage


class ExistingInventoryTests(unittest.TestCase):
    def test_all_installed_manifests_have_one_deterministic_row(self):
        first = coverage.build_report()
        second = coverage.build_report()
        self.assertEqual(first, second)
        self.assertEqual(first["version"], 2)
        self.assertEqual(first["unclassified"], [])
        self.assertEqual(first["stale"], [])
        source = sorted(path.parent.name for path in
                        (REPO / "src/plugins").glob("*/cifereca-plugin.json"))
        self.assertEqual([row["package"] for row in first["packages"]], source)
        self.assertEqual(first["stats"]["packages"], len(source))
        self.assertEqual(len({row["manifest_id"] for row in first["packages"]}),
                         len(source))

    def test_dimensions_are_source_evidence_not_successful_test_runs(self):
        report = coverage.build_report()
        for row in report["packages"]:
            self.assertIn(row["status"], coverage.STATES)
            self.assertNotEqual(row["status"], "covered")
            self.assertFalse(row["e2e_gtk_verified"])
            self.assertTrue(row["roles"])
            for key, state in row["dimensions"].items():
                self.assertIn(state, coverage.STATES, (row["package"], key))
                self.assertNotEqual(state, "covered", (row["package"], key))
            for key in coverage.EVIDENCE_FIELDS:
                for source in row[key]:
                    self.assertTrue((REPO / source).is_file(), source)
        self.assertEqual(report["stats"]["e2e_gtk_verified"], 0)

    def test_local_plugins_do_not_require_a_fake_http_server(self):
        rows = {r["package"]: r for r in coverage.build_report()["packages"]}
        for name in ("home-page", "diagnostics-page", "epub_optimizer",
                     "dev-updater", "runtime-kobo-adept"):
            self.assertEqual(rows[name]["dimensions"]["http_fixture"],
                             "not_applicable", name)
        self.assertEqual(rows["provider-bnf-pret"]["dimensions"]["http_fixture"],
                         "absent")  # MockTransport is NOT an HTTP fixture server.
        self.assertIn("baen", rows["baen"]["http_fixtures"])
        self.assertEqual(rows["baen"]["services"], [])

    def test_provider_and_gtk_scenarios_are_not_mislabeled_as_e2e_success(self):
        rows = {r["package"]: r for r in coverage.build_report()["packages"]}
        self.assertIn("provider.library", rows["provider-paris"]["roles"])
        self.assertIn("sync.loans", rows["provider-paris"]["capabilities"])
        self.assertEqual(rows["provider-paris"]["dimensions"]["failure_offline"],
                         "partial")
        self.assertIn("e2e/home_summary_acceptance.py",
                      rows["home-page"]["gtk_scenarios"])
        self.assertIn("ux-small", rows["provider-paris"]["fixture_profiles"])
        self.assertNotIn("e2e-smoke", rows["provider-kobo"]["fixture_profiles"])
        self.assertFalse(rows["home-page"]["e2e_gtk_verified"])

    def test_cli_reports_are_reproducible_and_strict_passes(self):
        a = StringIO()
        b = StringIO()
        with redirect_stdout(a):
            self.assertEqual(coverage.main(["--json", "--strict"]), 0)
        with redirect_stdout(b):
            self.assertEqual(coverage.main(["--json", "--strict"]), 0)
        self.assertEqual(a.getvalue(), b.getvalue())
        self.assertEqual(json.loads(a.getvalue())["stats"]["packages"],
                         coverage.build_report()["stats"]["packages"])


class SyntheticInventoryTests(unittest.TestCase):
    def make_repo(self, tmp: str, *, classified=True):
        repo = Path(tmp)
        plugin = repo / "src/plugins/example"
        plugin.mkdir(parents=True)
        (plugin / "cifereca-plugin.json").write_text(json.dumps({
            "package": {"id": "org.example.plugin"},
            "plugins": [{
                "id": "provider.example", "type": "provider.library",
                "capabilities": ["catalog.search"],
            }],
        }), encoding="utf-8")
        services = repo / "services.toml"
        services.write_text(
            "version = 1\n" +
            "".join(f"[service.{name}]\nplugins = []\n"
                    for name in sorted(coverage.SERVICE_NAMES)) +
            "[service.synthetic]\nplugins = [\"provider.example\"]\n",
            encoding="utf-8",
        )
        matrix = repo / "matrix.toml"
        matrix.write_text("version = 1\n" + (
            "[package.example]\ndata = []\ngap = \"fixture\"\n" if classified else ""
        ), encoding="utf-8")
        profiles = repo / "profiles"
        profiles.mkdir()
        (profiles / "offline.toml").write_text(
            "[services.synthetic]\nenabled = true\n", encoding="utf-8")
        return repo, plugin, services, matrix, profiles

    def report(self, repo, services, matrix, profiles):
        return coverage.build_report(
            repo_root=repo,
            matrix_path=matrix,
            packages_dir=repo / "src/plugins",
            catalog_path=services,
            profiles_dir=profiles,
        )

    def test_added_plugin_is_visible_and_fails_strict_until_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(
                tmp, classified=False)
            result = self.report(repo, services, matrix, profiles)
            self.assertEqual(result["unclassified"], ["example"])
            self.assertEqual(result["packages"][0]["services"], ["synthetic"])
            self.assertEqual(result["packages"][0]["fixture_profiles"], ["offline"])
            with patch.multiple(
                coverage, MATRIX=matrix, PACKAGES=repo / "src/plugins",
                CATALOG=services, PROFILES=profiles,
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(coverage.main(["--strict"]), 2)

    def test_stale_matrix_entry_is_detected_without_creating_a_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(tmp)
            matrix.write_text(matrix.read_text() +
                              "\n[package.removed]\ndata = []\n")
            result = self.report(repo, services, matrix, profiles)
            self.assertEqual(result["stale"], ["removed"])

    def test_evidence_sources_are_validated_not_taken_on_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(tmp)
            unit_dir = plugin / "tests"
            unit_dir.mkdir()
            proof = unit_dir / "test_contract.py"
            proof.write_text("def test_one(): pass\n", encoding="utf-8")
            ref = "src/plugins/example/tests/test_contract.py"
            base = matrix.read_text()
            for path in ("../../secret.py", "src/plugins/other/tests/test_fake.py",
                         "src/plugins/example/tests/test_missing.py", "/tmp/x.py"):
                matrix.write_text(base + f"contract_tests = [{json.dumps(path)}]\n")
                with self.assertRaises(ValueError, msg=path):
                    self.report(repo, services, matrix, profiles)
            matrix.write_text(base + f"contract_tests = [{json.dumps(ref)}]\n")
            result = self.report(repo, services, matrix, profiles)
            self.assertEqual(result["packages"][0]["contract_tests"], [ref])
            self.assertEqual(result["packages"][0]["dimensions"]["contract"],
                             "partial")
            proof.unlink()
            external = repo.parent / "outside-proof.py"
            external.write_text("def test_outside(): pass\n")
            try:
                proof.symlink_to(external)
                with self.assertRaises(ValueError):
                    self.report(repo, services, matrix, profiles)
            finally:
                external.unlink()

    def test_new_role_and_fixture_are_derived_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(tmp)
            report = self.report(repo, services, matrix, profiles)
            row = report["packages"][0]
            self.assertEqual(row["roles"], ["provider.library"])
            self.assertEqual(row["capabilities"], ["catalog.search"])
            self.assertEqual(row["services"], ["synthetic"])
            self.assertEqual(row["dimensions"]["http_fixture"], "partial")
            manifest_file = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_file.read_text())
            manifest["plugins"].append({
                "id": "ui.page.example", "type": "ui.page",
                "capabilities": ["ui.explore"],
            })
            manifest_file.write_text(json.dumps(manifest))
            other = self.report(repo, services, matrix, profiles)["packages"][0]
            self.assertIn("ui.page", other["roles"])
            self.assertIn("ui.page.example", other["ui_routes"])
            self.assertIn("ui.explore", other["capabilities"])
            self.assertEqual(other["dimensions"]["gtk_e2e"], "absent")

    def test_inventoried_test_without_execution_is_never_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(tmp)
            (plugin / "tests").mkdir()
            (plugin / "tests/test_unit.py").write_text(
                "def test_example(): pass\n", encoding="utf-8")
            row = self.report(repo, services, matrix, profiles)["packages"][0]
            self.assertEqual(row["unit_test_files"], 1)
            self.assertEqual(row["status"], "partial")
            self.assertEqual(row["dimensions"]["contract"], "absent")
            self.assertEqual(row["dimensions"]["failure_offline"], "absent")
            self.assertFalse(row["e2e_gtk_verified"])

    def test_data_declared_for_wrong_provider_does_not_pass_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, plugin, services, matrix, profiles = self.make_repo(tmp)
            matrix.write_text(
                "version = 1\n[package.example]\ndata = [\"paris\"]\n")
            with self.assertRaises(ValueError):
                self.report(repo, services, matrix, profiles)


if __name__ == "__main__":
    unittest.main()
