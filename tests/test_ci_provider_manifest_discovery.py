"""P0 #5057: unknown provider manifests are automatically part of the CI gate."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci_provider_coupling_delta_gate import _manifest_providers, gate


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


class ManifestProviderGateTest(unittest.TestCase):
    def test_unknown_provider_added_in_same_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.email", "ci@example.invalid")
            git(root, "config", "user.name", "CI Fixture")
            core = root / "src/cifereca/core/example.py"
            core.parent.mkdir(parents=True)
            core.write_text("def select(provider_id):\n    return provider_id\n")
            git(root, "add", ".")
            git(root, "commit", "-qm", "base")
            base = git(root, "rev-parse", "HEAD")
            manifest = root / "src/plugins/library-example/cifereca-plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "manifest": 1,
                "package": {"id": "org.example.provider.library-example"},
                "plugins": [{
                    "id": "provider.library.example", "type": "provider.library",
                    "entrypoint": "imaginary_library:PLUGIN",
                }],
            }))
            core.write_text('def select(provider_id):\n    if provider_id == "library.example":\n        return "special"\n    return provider_id\n')
            git(root, "add", ".")
            git(root, "commit", "-qm", "new provider and executable branch")
            head = git(root, "rev-parse", "HEAD")
            self.assertIn("library.example", _manifest_providers(root, head))
            report = gate(root, base, head)
            self.assertFalse(report["pass"])
            self.assertIn("comparisons:library.example",
                          report["violations"]["src/cifereca/core/example.py"])
            # Passive labels are not executable provider decisions.
            core.write_text('LABEL = "library.example"\n')
            git(root, "add", ".")
            git(root, "commit", "-qm", "passive literal")
            passive = git(root, "rev-parse", "HEAD")
            self.assertTrue(gate(root, base, passive)["pass"])

    def test_import_from_unknown_plugin_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.email", "ci@example.invalid")
            git(root, "config", "user.name", "CI Fixture")
            core = root / "src/cifereca/core/example.py"
            core.parent.mkdir(parents=True)
            core.write_text("VALUE = None\n")
            git(root, "add", ".")
            git(root, "commit", "-qm", "base")
            base = git(root, "rev-parse", "HEAD")
            manifest = root / "src/plugins/novel/cifereca-plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "package": {"id": "org.example.provider.novel"},
                "plugins": [{"id": "provider.novel", "type": "provider.library",
                             "entrypoint": "novel_engine:PLUGIN"}],
            }))
            core.write_text("import novel_engine\n")
            git(root, "add", ".")
            git(root, "commit", "-qm", "import new provider")
            head = git(root, "rev-parse", "HEAD")
            self.assertFalse(gate(root, base, head)["pass"])
            self.assertIn("imports:novel_engine",
                          gate(root, base, head)["violations"]["src/cifereca/core/example.py"])

    def test_malformed_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.email", "ci@example.invalid")
            git(root, "config", "user.name", "CI Fixture")
            manifest = root / "src/plugins/faulty/cifereca-plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{invalid json")
            git(root, "add", ".")
            git(root, "commit", "-qm", "invalid descriptor")
            with self.assertRaises(ValueError):
                _manifest_providers(root, git(root, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
