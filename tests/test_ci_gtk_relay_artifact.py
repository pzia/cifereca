"""Offline artifact privacy contract for the temporary GTK relay (#1143)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci-validate-gtk-relay-cache.py"
WORKFLOW = ROOT / ".github/workflows/gtk-e2e-on-approval.yml"


def _module():
    spec = importlib.util.spec_from_file_location("gtk_relay_cache", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GTKRelayArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / ".gtk-deb-cache"
        self.cache.mkdir()
        self.output = self.root / "safe.tar.gz"
        self.module = _module()
        (self.cache / self.module.MARKER).write_text(
            json.dumps(self.module.PROFILE), encoding="utf-8")
        for i in range(12):
            (self.cache / f"libgtk-test{i}_1.2.3_amd64.deb").write_bytes(
                b"!<arch>\n" + b"synthetic only")
        (self.cache / "partial").mkdir()

    def test_publishes_only_fixed_profile_and_debian_packages(self):
        self.assertEqual(self.module.build(self.cache, self.output), 12)
        with tarfile.open(self.output, "r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual(len(members), 13)
            self.assertTrue(all(member.isfile() for member in members))
            self.assertTrue(all(member.name.startswith(".gtk-deb-cache/") for member in members))
            self.assertEqual(
                {Path(member.name).name for member in members},
                {self.module.MARKER}
                | {f"libgtk-test{i}_1.2.3_amd64.deb" for i in range(12)})
            self.assertEqual(json.load(archive.extractfile(members[0])), self.module.PROFILE)

    def test_rejects_unexpected_or_private_files_without_creating_bundle(self):
        for relative, contents in (
            ("secrets.env", b"TOKEN=PRIVATE"),
            ("capture.png", b"PRIVATE IMAGE"),
            ("stdout.log", b"PRIVATE LOG"),
            ("private.json", b'{"credential":"PRIVATE"}'),
        ):
            with self.subTest(relative=relative):
                path = self.cache / relative
                path.write_bytes(contents)
                with self.assertRaises(ValueError):
                    self.module.build(self.cache, self.output)
                self.assertFalse(self.output.exists())
                path.unlink()

    def test_rejects_symlinks_and_nested_files(self):
        outside = self.root / "private-token"
        outside.write_text("PRIVATE")
        path = self.cache / "user_1.0_amd64.deb"
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        self.assertFalse(self.output.exists())
        path.unlink()
        sub = self.cache / "extra"
        sub.mkdir()
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        sub.rmdir()
        marker = self.cache / self.module.MARKER
        marker.unlink()
        marker.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)

    def test_rejects_bad_manifest_invalid_deb_and_overwriting(self):
        marker = self.cache / self.module.MARKER
        marker.write_text(json.dumps({**self.module.PROFILE, "token": "PRIVATE"}))
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        marker.write_text(json.dumps(self.module.PROFILE))
        package = self.cache / "libgtk-test0_1.2.3_amd64.deb"
        package.write_bytes(b"PRIVATE-DATA")
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        package.write_bytes(b"!<arch>\nsynthetic only")
        self.output.write_text("PRIVATE OLD FILE")
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        self.assertEqual(self.output.read_text(), "PRIVATE OLD FILE")

    def test_rejects_too_few_packages(self):
        for i in range(3):
            (self.cache / f"libgtk-test{i}_1.2.3_amd64.deb").unlink()
        with self.assertRaises(ValueError):
            self.module.build(self.cache, self.output)
        self.assertFalse(self.output.exists())

    def test_publication_remains_opt_in_and_excludes_source_checkout(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".github/gtk-e2e-run-request.txt", workflow)
        self.assertIn("types: [synchronize]", workflow)
        self.assertIn("scripts/ci-validate-gtk-relay-cache.py", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("git archive", workflow)
        self.assertNotIn("cifereca-gtk-source.tar.gz", workflow)
        self.assertIn("retention-days: 2", workflow)
        self.assertIn("persist-credentials: false", workflow)


if __name__ == "__main__":
    unittest.main()
