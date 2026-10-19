"""#2331: removed Jeunesse IDs cannot authorize a plugins-only release."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.ci_plugins_release_request import (
    ACTIVE_KIND, INACTIVE_KIND, ReleaseRequestRefused, resolve_request,
)

ROOT = Path(__file__).resolve().parents[1]
RETIRED = "org.cifereca.youth-source-discovery"
DIAGNOSTICS = "org.cifereca.ui.page.diagnostics"


class ReleaseRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.plugins = root / "plugins"
        self.plugins.mkdir()
        for dirname, package in [("diagnostics", DIAGNOSTICS), ("alpha", "org.cifereca.provider.alpha")]:
            p = self.plugins / dirname
            p.mkdir()
            (p / "cifereca-plugin.json").write_text(
                json.dumps({"package": {"id": package}}), encoding="utf-8"
            )
        self.request = root / "request.json"

    def write(self, payload):
        self.request.write_text(json.dumps(payload), encoding="utf-8")

    def resolve(self):
        return resolve_request("push", self.request, plugins_dir=self.plugins)

    def test_historical_request_is_archived_not_promoted_to_diagnostics_only(self):
        active = json.loads(
            (ROOT / ".github/plugins-only-release-request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(active["kind"], INACTIVE_KIND)
        self.assertNotIn("package_ids", active)
        self.assertNotIn("release_tag", active)
        archived = json.loads((ROOT / active["historical_request"]).read_text(encoding="utf-8"))
        self.assertEqual(archived["kind"], ACTIVE_KIND)
        self.assertEqual(archived["request_revision"], 4)
        self.assertEqual(archived["authorized_on"], "2026-10-09")
        self.assertIn(RETIRED, archived["package_ids"])
        self.assertIn(DIAGNOSTICS, archived["package_ids"])
        self.assertIsNone(
            resolve_request("push", ROOT / ".github/plugins-only-release-request.json",
                            plugins_dir=self.plugins)
        )

    def test_removed_and_unknown_ids_are_rejected_before_any_package_build(self):
        for packages in ([RETIRED], [DIAGNOSTICS, RETIRED],
                         [DIAGNOSTICS, "org.cifereca.never-installed"]):
            with self.subTest(packages=packages):
                self.write({"kind": ACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001",
                            "package_ids": packages})
                with self.assertRaisesRegex(ReleaseRequestRefused, "absent from active inventory"):
                    self.resolve()

    def test_only_explicit_active_manifest_ids_validate_without_publication(self):
        self.write({"kind": ACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001",
                    "package_ids": [DIAGNOSTICS]})
        self.assertEqual(self.resolve(), ("plugins-v0.9.44+dev.12-001", [DIAGNOSTICS]))
        self.assertEqual(
            resolve_request("workflow_dispatch", dispatch_tag="plugins-v0.9.44+dev.12-002",
                            dispatch_packages=DIAGNOSTICS, plugins_dir=self.plugins),
            ("plugins-v0.9.44+dev.12-002", [DIAGNOSTICS]),
        )

    def test_inactive_marker_cannot_hide_package_authorization(self):
        for payload in (
            {"kind": INACTIVE_KIND, "package_ids": [DIAGNOSTICS]},
            {"kind": INACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001"},
        ):
            with self.subTest(payload=payload):
                self.write(payload)
                with self.assertRaisesRegex(ReleaseRequestRefused, "Inactive request"):
                    self.resolve()
        self.write({"kind": INACTIVE_KIND, "historical_request": "docs/release-history/old.json"})
        self.assertIsNone(self.resolve())

    def test_wrong_kind_duplicates_and_missing_manifest_are_rejected(self):
        for payload in (
            {"kind": "unknown", "release_tag": "plugins-v0.9.44+dev.12-001",
             "package_ids": [DIAGNOSTICS]},
            {"kind": ACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001",
             "package_ids": [DIAGNOSTICS, DIAGNOSTICS]},
            {"kind": ACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001",
             "package_ids": [123]},
        ):
            with self.subTest(payload=payload):
                self.write(payload)
                with self.assertRaises(ReleaseRequestRefused):
                    self.resolve()
        self.write({"kind": ACTIVE_KIND, "release_tag": "plugins-v0.9.44+dev.12-001",
                    "package_ids": [DIAGNOSTICS]})
        with self.assertRaisesRegex(ReleaseRequestRefused, "No active plugin manifests"):
            resolve_request("push", self.request, plugins_dir=self.plugins / "missing")


if __name__ == "__main__":
    unittest.main()
