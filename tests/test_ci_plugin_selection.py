import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_select_plugin_tests.py"
spec = importlib.util.spec_from_file_location("plugin_selection", SCRIPT)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


class PluginSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.add_package("provider", [{"id": "service.provider", "type": "service.demo"}])
        self.add_package("consumer", [{"id": "service.consumer", "type": "consumer", "requires": [{"plugin": "service.provider"}]}])
        self.add_package("optional", [{"id": "service.optional", "type": "optional", "requires_contracts": [{"category": "service.demo", "mode": "optional"}]}])
        self.add_package("leaf", [{"id": "service.leaf", "type": "leaf"}])
        self.write_explicit(
            ["provider", "consumer", "optional", "leaf"],
            {"leaf": ["consumer"], "consumer": ["leaf"]},
        )

    def add_package(self, name, plugins):
        path = self.root / "src/plugins" / name
        (path / "tests").mkdir(parents=True)
        (path / "cifereca-plugin.json").write_text(json.dumps({"plugins": plugins}))
        (path / "module.py").write_text("# product\n")
        (path / "tests/test_local.py").write_text("# test\n")

    def write_explicit(self, audited, dependencies=None):
        path = self.root / selector.EXPLICIT_GRAPH
        path.parent.mkdir(parents=True, exist_ok=True)
        value = audited if dependencies is None else {
            "audited_product_packages": audited,
            "dependencies": dependencies,
        }
        path.write_text(json.dumps(value))

    def names(self, changes):
        targets, reason = selector.select(changes, self.root)
        return [Path(target).name for target in targets], reason

    def test_isolated_test_modules_and_docs_select_direct_packages(self):
        names, reason = self.names([("M", "src/plugins/provider/tests/test_local.py"), ("A", "src/plugins/leaf/tests/test_new.py"), ("M", "docs/note.md")])
        self.assertEqual(names, ["leaf", "provider"])
        self.assertIn("test modules", reason)

    def test_product_change_includes_direct_and_transitive_consumers(self):
        names, reason = self.names([("M", "src/plugins/provider/module.py")])
        self.assertEqual(names, ["consumer", "leaf", "optional", "provider"])
        self.assertIn("dependency closure", reason)

    def test_cycle_converges_and_union_is_stable(self):
        graph, _ = selector.dependency_graph(self.root)
        self.assertEqual(selector.affected_packages({"consumer"}, graph), ["consumer", "leaf"])
        names, _ = self.names([("M", "src/plugins/consumer/module.py"), ("M", "src/plugins/provider/tests/test_local.py")])
        self.assertEqual(names, ["consumer", "leaf", "provider"])

    def test_product_leaf_selects_only_leaf(self):
        self.write_explicit(["leaf"], {})
        self.assertEqual(self.names([("M", "src/plugins/leaf/module.py")])[0], ["leaf"])

    def test_unaudited_product_package_falls_back_but_its_test_module_is_targeted(self):
        self.write_explicit(["leaf"], {})
        names, reason = self.names([("M", "src/plugins/provider/module.py")])
        self.assertEqual(names, [])
        self.assertIn("not audited", reason)
        self.assertEqual(self.names([("M", "src/plugins/provider/tests/test_local.py")])[0], ["provider"])

    def test_manifest_helpers_deletions_renames_and_shared_changes_fall_back(self):
        cases = [
            ("M", "src/plugins/provider/cifereca-plugin.json"),
            ("M", "src/plugins/provider/tests/conftest.py"),
            ("M", "src/plugins/provider/tests/helper.py"),
            ("D", "src/plugins/provider/module.py"),
            ("R100", "src/plugins/provider/module.py"),
            ("M", "src/cifereca/plugin_api.py"),
            ("M", "scripts/ci_select_plugin_tests.py"),
            ("M", ".github/plugin-test-dependencies.json"),
        ]
        for status, path in cases:
            with self.subTest(path=path, status=status):
                self.assertEqual(self.names([("M", "src/plugins/leaf/module.py"), (status, path)])[0], [])

    def test_invalid_graph_falls_back(self):
        for explicit in (
            {"audited_product_packages": ["missing"], "dependencies": {}},
            {"audited_product_packages": ["consumer"], "dependencies": {"consumer": ["missing"]}},
            [],
        ):
            with self.subTest(explicit=explicit):
                self.write_explicit(explicit)
                names, reason = self.names([("M", "src/plugins/provider/module.py")])
                self.assertEqual(names, [])
                self.assertTrue(reason.startswith("full:"))

    def test_unknown_required_plugin_falls_back(self):
        self.add_package("broken", [{"id": "broken", "type": "broken", "requires": [{"plugin": "missing"}]}])
        self.assertEqual(self.names([("M", "src/plugins/provider/module.py")])[0], [])

    def test_repository_graph_has_no_retired_youth_packages(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertFalse(audited)
        self.assertFalse(any(name.startswith("youth-") for name in graph))
        self.assertTrue({"transport-http", "baen", "ebooksgratuits"} <= set(graph))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def test_real_git_diff_and_rename_fallback(self):
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        path = self.root / "src/plugins/provider/tests/test_local.py"
        path.write_text("# changed\n")
        self.git("commit", "-am", "test change")
        head = self.git("rev-parse", "HEAD")
        for event in ("push", "pull_request"):
            self.assertEqual(selector.select(selector.git_changes(self.root, base, head, event), self.root)[0], ["src/plugins/provider"])
        path.rename(path.with_name("test_renamed.py"))
        self.git("add", "-A")
        self.git("commit", "-m", "rename")
        renamed = self.git("rev-parse", "HEAD")
        self.assertEqual(selector.select(selector.git_changes(self.root, head, renamed, "push"), self.root)[0], [])
        with self.assertRaises(ValueError):
            selector.git_changes(self.root, base, head, "push")

    def test_manual_missing_and_zero_sha_cli_fall_back(self):
        for event, base in (("workflow_dispatch", ""), ("push", "0" * 40), ("push", "f" * 40)):
            result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "--event", event, "--base", base, "--head", "a" * 40], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["mode"], "full")


if __name__ == "__main__":
    unittest.main()
