"""Contrat réel KoboAdeptRuntime, avec fausse CLI ADEPT exclusivement locale.

Aucun compte Adobe, profil système, ACSM, clé DRM, ou liseuse n'est utilisé.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src", REPO / "src/plugins/runtime-kobo-adept"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import plugin_scenarios
from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_runtime import KoboAdeptRuntime, KoboAdeptError


class LocalContext:
    def __init__(self, command):
        self.config = {"command": command}
        self.events = []
        self.logger = None

    def report_diagnostic(self, message, status, **kwargs):
        self.events.append((message, status, kwargs))


class AdeptProfileSimulationTests(unittest.TestCase):
    def runtime(self, root):
        directory = Path(root)
        plugin_scenarios.create(directory)
        fixture = directory / "adept-profiles.json"
        script = SIM / "adept" / "fake_kobo_adept_cli.py"
        log = directory / "calls.jsonl"
        context = LocalContext([sys.executable, str(script), "--fixture", str(fixture),
                                "--log", str(log)])
        return KoboAdeptRuntime(context), context, fixture, log

    def test_real_runtime_reads_synthetic_profile_states_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, context, fixture, log = self.runtime(tmp)
            self.assertEqual(runtime.contract()["contract_version"], 8)
            self.assertTrue(runtime.capabilities()["capabilities"]["adept"]["profile_provisioning"]["available"])
            payload = runtime.profiles()
            profiles = {p["name"]: p for p in payload["profiles"]}
            self.assertEqual(set(profiles), {"nouveau", "incomplet", "pret", "sans-liseuse", "expiration"})
            for name in ("nouveau", "incomplet", "expiration"):
                self.assertFalse(runtime._profile_ready({"profile": profiles[name]}), name)
            for name in ("pret", "sans-liseuse"):
                self.assertTrue(runtime._profile_ready({"profile": profiles[name]}), name)
            self.assertEqual(profiles["nouveau"]["provisioning"]["state"], "created")
            self.assertEqual(profiles["sans-liseuse"]["devices"], [])
            self.assertTrue(all(p["account"] is None and p["user_uuid"] is None for p in profiles.values()))
            self.assertEqual(runtime.profile("pret")["profile"]["name"], "pret")
            self.assertTrue(context.events)
            self.assertTrue(log.is_file())

    def test_new_profile_provisioning_is_incomplete_and_never_claimed_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _, fixture, log = self.runtime(tmp)
            before = hashlib.sha256(fixture.read_bytes()).hexdigest()
            with self.assertRaises(AttentionRequired) as raised:
                runtime.provision_profile("nouveau-profil", "contact@example.test")
            self.assertEqual(raised.exception.code, "adept_profile_provisioning_incomplete")
            self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), before)
            entries = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertIn(["profile", "add"], [x["operation"] for x in entries])
            self.assertIn(["profile", "show"], [x["operation"] for x in entries])
            self.assertNotIn("contact@example.test", log.read_text())

    def test_association_and_removal_are_only_simulated_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _, fixture, log = self.runtime(tmp)
            before = fixture.read_bytes()
            mount = Path(tmp) / "fictional-reader"
            mount.mkdir()
            associated = runtime.associate_profile("pret", mount)
            # associate_profile() returns a fresh profile show payload,
            # not the internal CLI associate response with "simulated".
            self.assertEqual(associated["profile"]["name"], "pret")
            self.assertTrue(associated["profile"]["complete"])
            removed = runtime.remove_profile("pret")
            self.assertFalse(removed["removed"])
            self.assertTrue(removed["simulated"])
            self.assertEqual(fixture.read_bytes(), before)
            lines = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertFalse(any(x["force_requested"] for x in lines))
            self.assertNotIn(str(mount), log.read_text())
            self.assertEqual(runtime.profile("pret")["profile"]["name"], "pret")

    def test_absent_or_invalid_profile_never_creates_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _, _, _ = self.runtime(tmp)
            with self.assertRaises(KoboAdeptError) as missing:
                runtime.profile("inconnu")
            self.assertEqual(missing.exception.code, "profile_missing")
            with self.assertRaises(AttentionRequired):
                runtime.associate_profile("nouveau", "/definitely/not/mounted")

    def test_seed_and_manifest_are_reproducible(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            a = plugin_scenarios.create(Path(one))
            b = plugin_scenarios.create(Path(two))
            self.assertEqual(a, b)
            self.assertEqual(a["version"], plugin_scenarios.SCHEMA_VERSION)
            self.assertIn("adept-profiles.json", a["files"])
            self.assertEqual(len(a["files"]), 10)


if __name__ == "__main__":
    unittest.main()
