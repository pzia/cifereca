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
        for package in ("one", "two"):
            path = self.root / "src/plugins" / package
            (path / "tests").mkdir(parents=True)
            (path / "cifereca-plugin.json").write_text("{}")
            (path / "tests/test_local.py").write_text("# test\n")

    def test_isolated_test_modules_and_docs_select_union(self):
        targets, _ = selector.select([("M", "src/plugins/two/tests/test_local.py"), ("A", "src/plugins/one/tests/test_new.py"), ("M", "docs/note.md")], self.root)
        self.assertEqual(targets, ["src/plugins/one", "src/plugins/two"])

    def test_product_shared_helpers_and_deletions_fall_back(self):
        for status, path in [("M", "src/plugins/one/code.py"), ("M", "src/plugins/one/cifereca-plugin.json"), ("M", "src/plugins/one/tests/conftest.py"), ("M", "src/plugins/one/tests/helper.py"), ("D", "src/plugins/one/tests/test_local.py"), ("M", "src/cifereca/plugin_api.py"), ("M", "scripts/ci_select_plugin_tests.py"), ("R100", "src/plugins/one/tests/test_local.py")]:
            with self.subTest(path=path, status=status):
                self.assertEqual(selector.select([("M", "src/plugins/two/tests/test_local.py"), (status, path)], self.root)[0], [])

    def test_empty_and_missing_package_fall_back(self):
        for changes in ([], [("M", "docs/note.md")], [("M", "src/plugins/missing/tests/test_local.py")]):
            self.assertEqual(selector.select(changes, self.root)[0], [])

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def test_real_git_diff_and_rename_fallback(self):
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        path = self.root / "src/plugins/one/tests/test_local.py"
        path.write_text("# changed\n")
        self.git("commit", "-am", "test change")
        head = self.git("rev-parse", "HEAD")
        for event in ("push", "pull_request"):
            self.assertEqual(selector.select(selector.git_changes(self.root, base, head, event), self.root)[0], ["src/plugins/one"])
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
