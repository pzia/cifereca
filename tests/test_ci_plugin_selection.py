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

    def test_readme_changes_do_not_expand_isolated_test_changes(self):
        names, reason = self.names([
            ("M", "src/plugins/provider/tests/test_local.py"),
            ("M", "src/plugins/consumer/README.md"),
            ("A", "src/plugins/README.md"),
        ])
        self.assertEqual(names, ["provider"])
        self.assertIn("test modules", reason)
        # Non-README assets and deletions still trigger exhaustive checks.
        names, reason = self.names([
            ("M", "src/plugins/provider/tests/test_local.py"),
            ("M", "src/plugins/consumer/notes.md"),
        ])
        self.assertEqual(names, ["consumer", "leaf", "provider"])
        self.assertIn("dependency closure", reason)
        self.assertEqual(self.names([("D", "src/plugins/consumer/README.md")])[0], [])
        self.assertIn("deletion", self.names([("D", "src/plugins/consumer/README.md")])[1])
        self.assertEqual(self.names([("M", "src/plugins/consumer/README.md")])[1],
                         "skip: only plugin README edits")

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
        self.assertEqual(audited, {"dev-updater", "literary-criticism"})
        self.assertFalse(any(name.startswith("youth-") for name in graph))
        self.assertTrue({"transport-http", "baen", "ebooksgratuits"} <= set(graph))

    def test_audited_literary_criticism_stays_isolated_from_other_plugin_suites(self):
        # Specific package proved independent. Any newly discovered consumer
        # must trigger an explicit graph audit before maintaining this scope.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertEqual(audited, {"dev-updater", "literary-criticism"})
        self.assertEqual(
            selector.affected_packages({"literary-criticism"}, graph),
            ["literary-criticism"],
        )
        chosen, reason = selector.select([
            ("M", "src/plugins/literary-criticism/criticism_feeds.py"),
        ], ROOT)
        self.assertEqual(chosen, ["src/plugins/literary-criticism"])
        self.assertIn("dependency closure", reason)
        # All other product packages remain conservatively unaudited.
        chosen, reason = selector.select([
            ("M", "src/plugins/baen/cifereca_baen.py"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("not audited", reason)
        chosen, reason = selector.select([
            ("M", "src/plugins/literary-criticism/criticism_feeds.py"),
            ("M", "src/plugins/baen/cifereca_baen.py"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("not audited", reason)
        for status, path in (
            ("M", "src/plugins/literary-criticism/cifereca-plugin.json"),
            ("D", "src/plugins/literary-criticism/criticism_feeds.py"),
            ("R100", "src/plugins/literary-criticism/criticism_feeds.py"),
        ):
            with self.subTest(status=status, path=path):
                self.assertEqual(selector.select([(status, path)], ROOT)[0], [])

    def test_audited_literary_python_imports_have_no_unaudited_consumers(self):
        # A new explicit cross-package import requires adding the consumer to
        # the dependency graph; do not silently retain a selective green gate.
        tokens = (
            "literary_criticism", "literary-criticism",
            "criticism_feeds", "criticism_page", "criticism_llm",
            "criticism.index", "criticism.source",
        )
        for file in sorted((ROOT / "src/plugins").glob("*/**/*.py")):
            if file.relative_to(ROOT / "src/plugins").parts[0] == "literary-criticism":
                continue
            content = file.read_text(encoding="utf-8", errors="replace")
            self.assertFalse(
                any(token in content for token in tokens),
                f"Unexpected new literary-criticism consumer: {file}",
            )

    def test_audited_dev_updater_is_independent_of_other_plugin_suites(self):
        # The opt-in releases page only imports its own helper, the Core
        # plugin API and ordinary HTTPX/packaging dependencies; no other
        # package consumes its ui.page.dev-updater contribution.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("dev-updater", audited)
        self.assertEqual(
            selector.affected_packages({"dev-updater"}, graph),
            ["dev-updater"],
        )
        chosen, reason = selector.select([
            ("M", "src/plugins/dev-updater/github_releases.py"),
        ], ROOT)
        self.assertEqual(chosen, ["src/plugins/dev-updater"])
        self.assertIn("dependency closure", reason)
        # A non-audited runtime change cannot piggy-back on a selected suite.
        for changes in (
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/dev-updater/github_releases.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/dev-updater/cifereca-plugin.json")],
            [("D", "src/plugins/dev-updater/github_releases.py")],
            [("R100", "src/plugins/dev-updater/github_releases.py")],
            [("M", "src/plugins/dev-updater/tests/helper.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_audited_dev_updater_has_no_cross_package_consumers(self):
        # Stop the optimization if a new package imports or references this
        # plugin directly, including in manifests, shell scripts or tests.
        # Generic ui.page registration is not a plugin-specific dependency.
        tokens = (
            "dev-updater", "dev_updater", "dev_updater_page",
            "github_releases", "ui.page.dev-updater",
            "org.cifereca.dev-updater",
        )
        source_root = ROOT / "src/plugins"
        for file in sorted(source_root.glob("*/*")):
            package = file.relative_to(source_root).parts[0]
            if package == "dev-updater":
                continue
            sources = file.rglob("*") if file.is_dir() else (file,)
            for source in sources:
                if source.is_file() and source.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = source.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"Unexpected dev-updater consumer: {source}",
                    )

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

    def test_readme_only_selection_reports_skip_in_cli(self):
        # Synthetic Git history rather than trusting a path-only filter.
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        readme = self.root / "src/plugins/provider/README.md"
        readme.write_text("plugin documentation\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "docs")
        head = self.git("rev-parse", "HEAD")
        output = self.root / "result.txt"
        run = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root),
                              "--event", "push", "--base", base, "--head", head,
                              "--github-output", str(output)],
                             capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout)["mode"], "skip")
        self.assertIn("plugin_mode=skip", output.read_text(encoding="utf-8"))

    def test_manual_missing_and_zero_sha_cli_fall_back(self):
        for event, base in (("workflow_dispatch", ""), ("push", "0" * 40), ("push", "f" * 40)):
            result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "--event", event, "--base", base, "--head", "a" * 40], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["mode"], "full")


if __name__ == "__main__":
    unittest.main()
