"""P0 #787: exercise the *real* dev-updater and Core plugin CLI in a sandbox.

Unlike test_dev_partial_plugin_updates.py, installation and smoke use real
subprocesses, package extraction and package store. The Core release here is
a synthetic *runtime fixture*, not a published GitHub release.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from scripts.package_partial_plugins import build_partial_snapshot


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "org.cifereca.diagnostics.p0-updater-fixture"
SECOND_PACKAGE_ID = "org.cifereca.diagnostics.p0-updater-secondary"


class PluginsOnlyInstalledCliSmoke(unittest.TestCase):
    def setUp(self):
        if os.name != "posix":
            self.skipTest("release symlinks and shell CLI target a POSIX runtime")
        self.temp = tempfile.TemporaryDirectory(prefix="cifereca-p0-updater-")
        self.addCleanup(self.temp.cleanup)
        self.sandbox = Path(self.temp.name)
        self.runtime = self.sandbox / "runtime"
        self.current = self.runtime / "releases" / "baseline"
        self.current.mkdir(parents=True)
        (self.runtime / "current").symlink_to("releases/baseline")
        self.data_home = self.sandbox / "data"
        self.state_home = self.sandbox / "state"
        self.fixture_dir = self.sandbox / "fixture-plugins"
        self.fixture_dir.mkdir()
        self.version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.project_file = self.current / "pyproject.toml"
        self._core_metadata(self.version)
        api = self.current / "src" / "cifereca" / "plugin_api" / "version.py"
        api.parent.mkdir(parents=True)
        api.write_text((ROOT / "src/cifereca/plugin_api/version.py").read_text(encoding="utf-8"), encoding="utf-8")

        self.fail_list = self.sandbox / "fail-list"
        self.corrupt_install = self.sandbox / "corrupt-install"
        self.stale_pointer = self.sandbox / "stale-pointer"
        self.cli = self.current / ".venv" / "bin" / "cifereca-cli"
        self.cli.parent.mkdir(parents=True)
        self.cli.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"from pathlib import Path\n"
            f"if sys.argv[1:3] == ['plugin', 'list'] and Path({str(self.fail_list)!r}).exists():\n"
            "    raise SystemExit(44)\n"
            "from cifereca.cli import main\n"
            "result = main()\n"
            f"if result == 0 and sys.argv[1:3] == ['plugin', 'install'] and Path({str(self.corrupt_install)!r}).exists():\n"
            "    from cifereca.paths import data_dir\n"
            "    for installed in (data_dir() / 'plugins' / 'packages').rglob('fixture.py'):\n"
            "        installed.write_text('corrupted post-install\\n', encoding='utf-8')\n"
            f"if result == 0 and sys.argv[1:3] == ['plugin', 'install'] and Path({str(self.stale_pointer)!r}).exists():\n"
            "    from cifereca.paths import data_dir\n"
            "    for pointer in (data_dir() / 'plugins' / 'packages').glob('*/current.json'):\n"
            "        pointer.write_text('{\"active\": \"0.0.0\"}', encoding='utf-8')\n"
            "raise SystemExit(result)\n",
            encoding="utf-8",
        )
        self.cli.chmod(0o755)

        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.sandbox),
            "XDG_CONFIG_HOME": str(self.sandbox / "config"),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CACHE_HOME": str(self.sandbox / "cache"),
            "XDG_STATE_HOME": str(self.state_home),
            "CIFERECA_INSTALL_ROOT": str(self.runtime),
            "CIFERECA_BIN_DIR": str(self.sandbox / "bin"),
            "PYTHONPATH": os.pathsep.join(
                filter(None, (str(ROOT / "src"), self.env.get("PYTHONPATH", "")))
            ),
        })
        self.marker = self.sandbox / "config" / "cifereca" / "user-state.json"
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('{"user":"offline fixture"}\n', encoding="utf-8")

    def _core_metadata(self, version):
        (self.current / "VERSION").write_text(version + "\n", encoding="utf-8")
        self.project_file.write_text(
            '[project]\nname="cifereca"\nversion="' + version +
            '"\n[project.scripts]\ncifereca="fixture:main"\n'
            'cifereca-cli="fixture:main"\ncifereca-update="fixture:main"\n',
            encoding="utf-8",
        )

    def _snapshot(self, version: str, *, multi: bool = False) -> Path:
        fixture = self.fixture_dir / "fixture"
        fixture.mkdir(exist_ok=True)
        manifest = {
            "manifest": 1,
            "package": {
                "id": PACKAGE_ID, "version": version,
                "name": "P0 Offline Diagnostic", "publisher": "Tests",
                "plugin_api": ">=1.4,<2",
            },
            "plugins": [{
                "id": "diagnostics.p0-updater-fixture",
                "type": "diagnostics.check",
                "entrypoint": "fixture:PLUGIN",
                "version": version,
                "enabled_by_default": True,
                "help_text": "Offline diagnostic for the P0 updater acceptance test.",
            }],
        }
        (fixture / "cifereca-plugin.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        (fixture / "fixture.py").write_text(
            "class Health:\n"
            "    def activate(self, context):\n        return self\n"
            "    def deactivate(self):\n        pass\n"
            "    def checks(self):\n        return ()\n"
            "PLUGIN = Health()\n",
            encoding="utf-8",
        )
        packages = [PACKAGE_ID]
        if multi:
            other = self.fixture_dir / "secondary"
            other.mkdir(exist_ok=True)
            other_manifest = json.loads(json.dumps(manifest))
            other_manifest["package"]["id"] = SECOND_PACKAGE_ID
            other_manifest["package"]["name"] = "P0 Offline Secondary"
            other_manifest["plugins"][0]["id"] = "diagnostics.p0-updater-secondary"
            (other / "cifereca-plugin.json").write_text(
                json.dumps(other_manifest), encoding="utf-8",
            )
            (other / "fixture.py").write_text(
                (fixture / "fixture.py").read_text(encoding="utf-8"), encoding="utf-8",
            )
            packages.append(SECOND_PACKAGE_ID)
        return build_partial_snapshot(
            packages, plugins_dir=self.fixture_dir,
            output_dir=self.sandbox / ("dist-" + version + ("-multi" if multi else "")),
            snapshot_id="p0-offline-preview-" + version + ("-multi" if multi else ""),
        )

    def _update_cli(self, archive: Path, *, sha: str | None = None, strict: bool = False) -> tuple[int, dict, str]:
        digest = sha if sha is not None else hashlib.sha256(archive.read_bytes()).hexdigest()
        command = [
            sys.executable, "-m", "cifereca.dev_updater", str(archive),
            "--expected-sha256", digest, "--json",
        ]
        if strict:
            command.append("--require-plugins-only")
        proc = subprocess.run(
            command,
            cwd=self.sandbox, env=self.env, capture_output=True, text=True,
            timeout=60, check=False,
        )
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            self.fail(f"updater did not return JSON: rc={proc.returncode}, stdout={proc.stdout[-1200:]}, stderr={proc.stderr[-1200:]}")
            raise exc
        return proc.returncode, payload, proc.stderr

    def _inventory(self) -> list[dict]:
        proc = subprocess.run(
            [str(self.cli), "plugin", "list"],
            cwd=self.sandbox, env=self.env, capture_output=True, text=True,
            timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-1400:])
        return json.loads(proc.stdout)

    def _store(self, package_id: str = PACKAGE_ID) -> Path:
        return self.data_home / "cifereca" / "plugins" / "packages" / package_id

    def _installed_fingerprint(self, package_id: str = PACKAGE_ID) -> list[tuple[str, str]]:
        root = self._store(package_id)
        return sorted(
            (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()
        )

    def test_cli_installs_compatible_indexed_preview_without_core_or_data_mutation(self):
        source = self._snapshot("1.2.3")
        current = (self.runtime / "current").resolve()
        rc, result, error = self._update_cli(source)
        self.assertEqual(rc, 0, (result, error[-1600:]))
        self.assertTrue(result["ok"])
        self.assertTrue(result["plugins_only_indexed"])
        self.assertFalse(result["core_changed"])
        self.assertEqual(result["plugins"], [PACKAGE_ID])
        self.assertEqual((self.runtime / "current").resolve(), current)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')
        self.assertTrue(self._store().is_dir())
        self.assertIn(PACKAGE_ID, [row["package_id"] for row in self._inventory()])

    def test_incompatible_core_is_refused_before_any_store_or_backup(self):
        source = self._snapshot("1.2.3")
        self._core_metadata("0.0.1")
        rc, result, _ = self._update_cli(source)
        self.assertNotEqual(rc, 0)
        self.assertFalse(result["ok"])
        self.assertIn("Core cible", result["error"]["message"])
        self.assertFalse(self._store().exists())
        self.assertFalse((self.state_home / "cifereca" / "dev-updates").exists())
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')

    def test_real_cli_smoke_failure_restores_previous_plugin_package(self):
        baseline = self._snapshot("1.2.3")
        rc, result, stderr = self._update_cli(baseline)
        self.assertEqual(rc, 0, (result, stderr[-1500:]))
        initial = sorted(
            (p.relative_to(self._store()).as_posix(),
             hashlib.sha256(p.read_bytes()).hexdigest())
            for p in self._store().rglob("*") if p.is_file()
        )
        self.assertTrue(initial)
        second = self._snapshot("1.2.4")
        self.fail_list.touch()
        try:
            rc, failed, _ = self._update_cli(second)
        finally:
            self.fail_list.unlink()
        self.assertNotEqual(rc, 0)
        self.assertFalse(failed["ok"])
        self.assertIn("smoke plugins", failed["error"]["message"])
        after = sorted(
            (p.relative_to(self._store()).as_posix(),
             hashlib.sha256(p.read_bytes()).hexdigest())
            for p in self._store().rglob("*") if p.is_file()
        )
        self.assertEqual(after, initial, "rollback must restore the full package tree")
        self.assertEqual((self.runtime / "current").resolve(), self.current)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')
        self.assertIn(PACKAGE_ID, [row["package_id"] for row in self._inventory()])

    def test_trusted_sha_mismatch_refused_before_backup(self):
        source = self._snapshot("1.2.3")
        rc, result, _ = self._update_cli(source, sha="0" * 64)
        self.assertNotEqual(rc, 0)
        self.assertIn("SHA-256", result["error"]["message"])
        self.assertFalse(self._store().exists())
        self.assertFalse((self.state_home / "cifereca" / "dev-updates").exists())

    def test_strict_cli_multi_package_keeps_inactive_state_and_core_data(self):
        baseline = self._snapshot("1.2.3")
        rc, result, stderr = self._update_cli(baseline, strict=True)
        self.assertEqual(rc, 0, (result, stderr[-1200:]))
        pointer = self._store() / "current.json"
        pointer.write_text('{"active": null, "previous": "1.2.3"}\n', encoding="utf-8")
        runtime_before = (self.runtime / "current").resolve()
        update = self._snapshot("1.2.4", multi=True)
        rc, result, stderr = self._update_cli(update, strict=True)
        self.assertEqual(rc, 0, (result, stderr[-1500:]))
        self.assertTrue(result["strict_plugins_only"])
        self.assertTrue(result["trusted_digest_verified"])
        self.assertEqual(result["plugins"], sorted([PACKAGE_ID, SECOND_PACKAGE_ID]))
        self.assertFalse(result["core_changed"])
        self.assertEqual((self.runtime / "current").resolve(), runtime_before)
        self.assertIsNone(json.loads(pointer.read_text())["active"])
        self.assertTrue((self._store() / "1.2.4" / "fixture.py").is_file())
        self.assertEqual(
            json.loads((self._store(SECOND_PACKAGE_ID) / "current.json").read_text())["active"],
            "1.2.4",
        )
        self.assertIn(SECOND_PACKAGE_ID, [row["package_id"] for row in self._inventory()])
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')

    def test_strict_cli_rejects_old_core_for_two_packages_without_mutation(self):
        baseline = self._snapshot("1.2.3", multi=True)
        self._core_metadata("0.0.1")
        rc, result, _ = self._update_cli(baseline, strict=True)
        self.assertNotEqual(rc, 0)
        self.assertIn("Core cible", result["error"]["message"])
        for package_id in (PACKAGE_ID, SECOND_PACKAGE_ID):
            self.assertFalse(self._store(package_id).exists())
        self.assertFalse((self.state_home / "cifereca" / "dev-updates").exists())
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')

    def test_strict_cli_installed_byte_tampering_rolls_back_to_previous_version(self):
        baseline = self._snapshot("1.2.3")
        rc, result, stderr = self._update_cli(baseline, strict=True)
        self.assertEqual(rc, 0, (result, stderr[-1600:]))
        before = self._installed_fingerprint()
        changed = self._snapshot("1.2.4")
        self.corrupt_install.touch()
        try:
            rc, rejected, _ = self._update_cli(changed, strict=True)
        finally:
            self.corrupt_install.unlink()
        self.assertNotEqual(rc, 0)
        self.assertIn("intégrité/version/activation", rejected["error"]["message"])
        self.assertEqual(self._installed_fingerprint(), before)
        self.assertEqual((self.runtime / "current").resolve(), self.current)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), '{"user":"offline fixture"}\n')

    def test_strict_cli_wrong_active_version_rolls_back(self):
        baseline = self._snapshot("1.2.3")
        rc, result, stderr = self._update_cli(baseline, strict=True)
        self.assertEqual(rc, 0, (result, stderr[-1500:]))
        before = self._installed_fingerprint()
        changed = self._snapshot("1.2.4")
        self.stale_pointer.touch()
        try:
            rc, rejected, _ = self._update_cli(changed, strict=True)
        finally:
            self.stale_pointer.unlink()
        self.assertNotEqual(rc, 0)
        self.assertIn("intégrité/version/activation", rejected["error"]["message"])
        self.assertEqual(self._installed_fingerprint(), before)
        self.assertIn(PACKAGE_ID, [row["package_id"] for row in self._inventory()])



if __name__ == "__main__":
    unittest.main()
