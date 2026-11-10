import importlib.util
import re
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

    def test_optional_recommendation_closes_to_provider_and_transitive_consumers(self):
        self.add_package("recommended", [
            {"id": "service.recommended", "type": "optional.runtime"},
        ])
        self.add_package("recommender", [
            {"id": "service.recommender", "type": "consumer",
             "recommends": [{"plugin": "service.recommended", "version": ">=1"}]},
        ])
        self.add_package("downstream", [
            {"id": "service.downstream", "type": "consumer",
             "requires": [{"plugin": "service.recommender"}]},
        ])
        # Include newly added packages in synthetic audit; no shortcuts for
        # unqualified product packages in the actual repository.
        self.write_explicit(
            ["provider", "consumer", "optional", "leaf",
             "recommended", "recommender", "downstream"],
            {"leaf": ["consumer"], "consumer": ["leaf"]},
        )
        names, reason = self.names([
            ("M", "src/plugins/recommended/module.py"),
        ])
        self.assertEqual(names, ["downstream", "recommended", "recommender"])
        self.assertIn("dependency closure", reason)
        # Changing the recommender must not imply testing the dependency.
        names, _ = self.names([("M", "src/plugins/recommender/module.py")])
        self.assertEqual(names, ["downstream", "recommender"])

    def test_malformed_recommendations_fail_closed(self):
        for index, bad in enumerate(
            (None, "unexpected", [None], [{}], [{"plugin": 4}])
        ):
            with self.subTest(bad=bad):
                # Each package name is unique: malformed cases stay in the
                # graph to demonstrate fail-closed behaviour for all forms.
                self.add_package(f"invalid-{index}", [
                    {"id": f"service.invalid-{index}", "type": "consumer",
                     "recommends": bad},
                ])
                names, reason = self.names([
                    ("M", "src/plugins/provider/module.py"),
                ])
                self.assertEqual(names, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_unknown_recommended_id_does_not_claim_a_local_provider(self):
        self.add_package("external", [
            {"id": "service.external", "type": "consumer",
             "recommends": [{"plugin": "outside.repository"}]},
        ])
        graph, _ = selector.dependency_graph(self.root)
        self.assertEqual(graph["external"], set())

    def test_real_kobo_optional_ids_do_not_fake_a_runtime_dependency(self):
        # The manifests recommend drm.ade, but the local runtime declares
        # runtime.kobo-adept: these plugin IDs are NOT aliases.
        # Conservative explicit edges document the actual Core broker
        # interactions with Kobo/Paris/Numilog and diagnostics-page.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("runtime-kobo-adept", audited)
        expected = [
            "diagnostics-page", "home-page", "provider-kobo",
            "provider-numilog", "provider-paris", "runtime-kobo-adept",
        ]
        self.assertEqual(
            selector.affected_packages({"runtime-kobo-adept"}, graph),
            expected,
        )
        for module in ("cifereca_kobo_adept_runtime.py",
                       "cifereca_kobo_adept_profiles.py",
                       "cifereca_kobo_adept_health.py"):
            with self.subTest(module=module):
                names, reason = selector.select([
                    ("M", f"src/plugins/runtime-kobo-adept/{module}"),
                ], ROOT)
                self.assertEqual(names,
                                 [f"src/plugins/{name}" for name in expected])
                self.assertIn("dependency closure", reason)
        for consumer in ("provider-kobo", "provider-paris",
                         "provider-numilog", "diagnostics-page"):
            self.assertIn("runtime-kobo-adept", graph[consumer])
        for changes in (
            [("M", "src/plugins/runtime-kobo-adept/cifereca-plugin.json")],
            [("M", "src/plugins/runtime-kobo-adept/tests/conftest.py")],
            [("D", "src/plugins/runtime-kobo-adept/cifereca_kobo_adept_runtime.py")],
            [("R100", "src/plugins/runtime-kobo-adept/cifereca_kobo_adept_runtime.py")],
            [("M", "src/plugins/runtime-kobo-adept/cifereca_kobo_adept_runtime.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
        ):
            with self.subTest(changes=changes):
                names, reason = selector.select(changes, ROOT)
                self.assertEqual(names, [])
                self.assertTrue(reason.startswith("full:"), reason)

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

    def test_malformed_dependency_graph_values_fail_closed(self):
        malformed = (
            {"audited_product_packages": [None], "dependencies": {}},
            {"audited_product_packages": ["provider", "provider"], "dependencies": {}},
            {"audited_product_packages": ["provider"], "dependencies": {"consumer": [None]}},
            {"audited_product_packages": ["provider"], "dependencies": {"consumer": "provider"}},
        )
        for value in malformed:
            with self.subTest(value=value):
                self.write_explicit(value)
                names, reason = self.names([("M", "src/plugins/provider/module.py")])
                self.assertEqual(names, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_malformed_manifest_dependencies_fail_closed(self):
        invalid_plugins = (
            [None],
            [{"id": "bad", "type": "bad", "requires": None}],
            [{"id": "bad", "type": "bad", "requires": [None]}],
            [{"id": "bad", "type": "bad", "requires": [{"plugin": []}]}],
            [{"id": "bad", "type": "bad", "requires_contracts": None}],
            [{"id": "bad", "type": "bad", "requires_contracts": [None]}],
        )
        for index, plugins in enumerate(invalid_plugins):
            with self.subTest(plugins=plugins):
                self.add_package(f"broken-{index}", plugins)
                names, reason = self.names([("M", "src/plugins/provider/module.py")])
                self.assertEqual(names, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_unknown_required_plugin_falls_back(self):
        self.add_package("broken", [{"id": "broken", "type": "broken", "requires": [{"plugin": "missing"}]}])
        self.assertEqual(self.names([("M", "src/plugins/provider/module.py")])[0], [])

    def test_baen_audited_download_and_home_consumers(self):
        # Baen is a provider.library consumer of transport.http; the
        # home-page optional contract observes Baen's provider interface.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("baen", audited)
        self.assertIn("transport-http", graph["baen"])
        self.assertIn("baen", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"baen"}, graph),
            ["baen", "home-page"],
        )
        for path in ("cifereca_baen.py",):
            with self.subTest(path=path):
                names, reason = selector.select(
                    [("M", "src/plugins/baen/" + path)], ROOT,
                )
                self.assertEqual(names, [
                    "src/plugins/baen", "src/plugins/home-page",
                ])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/baen/cifereca-plugin.json")],
            [("M", "src/plugins/baen/tests/conftest.py")],
            [("D", "src/plugins/baen/cifereca_baen.py")],
            [("R100", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/baen/cifereca_baen.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
        ):
            with self.subTest(changes=changes):
                targets, reason = selector.select(changes, ROOT)
                self.assertEqual(targets, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_baen_has_no_unaudited_plugin_consumers(self):
        tokens = (
            "provider.baen", "org.cifereca.provider.baen",
            "cifereca_baen", "BaenSession", "BaenPlugin",
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "baen":
                continue
            for source in package_dir.rglob("*"):
                if source.is_file() and source.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = source.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens),
                        f"Unexpected Baen provider consumer: {source}",
                    )

    def test_repository_graph_has_no_retired_youth_packages(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertEqual(audited, {"baen", "browser-recorder", "constellations", "dev-updater", "diagnostics-page", "ebooksgratuits", "epub_optimizer", "home-page", "literary-criticism", "maintenance-library-audit", "provider-bnf-catalogue", "provider-bnf-pret", "provider-gallica", "provider-gutenberg", "provider-kobo", "provider-numilog", "provider-paris", "provider-reference", "recommendations", "runtime-kobo-adept", "transport-http"})
        self.assertFalse(any(name.startswith("youth-") for name in graph))
        self.assertTrue({"transport-http", "baen", "ebooksgratuits"} <= set(graph))

    def test_audited_literary_criticism_stays_isolated_from_other_plugin_suites(self):
        # Specific package proved independent. Any newly discovered consumer
        # must trigger an explicit graph audit before maintaining this scope.
        graph, _ = selector.dependency_graph(ROOT)
        self.assertEqual(
            selector.affected_packages({"literary-criticism"}, graph),
            ["literary-criticism"],
        )
        chosen, reason = selector.select([
            ("M", "src/plugins/literary-criticism/criticism_feeds.py"),
        ], ROOT)
        self.assertEqual(chosen, ["src/plugins/literary-criticism"])
        self.assertIn("dependency closure", reason)
        # A manifest modification still requires the complete plugin gate.
        chosen, reason = selector.select([
            ("M", "src/plugins/provider-kobo/cifereca-plugin.json"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("manifest graph changed", reason)
        chosen, reason = selector.select([
            ("M", "src/plugins/literary-criticism/criticism_feeds.py"),
            ("M", "src/plugins/provider-kobo/cifereca-plugin.json"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("manifest graph changed", reason)
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
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/dev-updater/github_releases.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
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

    def test_audited_maintenance_tools_have_no_other_plugin_consumers(self):
        # The package publishes Core-consumed maintenance.tool contracts,
        # never providers or imports used by other plugin package suites.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("maintenance-library-audit", audited)
        self.assertEqual(
            selector.affected_packages({"maintenance-library-audit"}, graph),
            ["maintenance-library-audit"],
        )
        for product in ("cifereca_library_audits.py", "cifereca_dedup_recovery.py"):
            with self.subTest(product=product):
                selected, reason = selector.select([
                    ("M", "src/plugins/maintenance-library-audit/" + product),
                ], ROOT)
                self.assertEqual(selected, ["src/plugins/maintenance-library-audit"])
                self.assertIn("dependency closure", reason)
        # Manifest changes still trigger exhaustive selection.
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/maintenance-library-audit/cifereca_library_audits.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/maintenance-library-audit/cifereca-plugin.json")],
            [("M", "src/plugins/maintenance-library-audit/tests/conftest.py")],
            [("D", "src/plugins/maintenance-library-audit/cifereca_library_audits.py")],
            [("R100", "src/plugins/maintenance-library-audit/cifereca_library_audits.py")],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_audited_maintenance_tool_runtime_has_no_cross_package_references(self):
        # A later package consuming a specific audit, dedup tool or helper
        # must invalidate this proof before a scoped green gate can persist.
        tokens = (
            "maintenance-library-audit", "cifereca_library_audits",
            "cifereca_dedup_recovery", "maintenance.audit.",
            "maintenance.repair.naming", "maintenance.cleanup.duplicates",
            "library.duplicates.recover",
            "org.cifereca.maintenance.library-audit",
        )
        source_root = ROOT / "src/plugins"
        for item in sorted(source_root.glob("*/*")):
            package = item.relative_to(source_root).parts[0]
            if package == "maintenance-library-audit":
                continue
            sources = item.rglob("*") if item.is_dir() else (item,)
            for file in sources:
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"Unexpected maintenance tool plugin consumer: {file}",
                    )

    def test_epub_optimizer_profiles_and_transformers_are_one_package(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("epub_optimizer", audited)
        self.assertEqual(
            selector.affected_packages({"epub_optimizer"}, graph),
            ["epub_optimizer"],
        )
        # Required device.profile contracts are implemented in this package;
        # UI requires the two transformer IDs from the same package.
        for module in (
            "cifereca_epub_optimizer_transformer.py",
            "cifereca_pdf_optimizer_transformer.py",
            "cifereca_epub_optimizer_profiles.py",
            "cifereca_epub_optimizer_plugin.py",
        ):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/epub_optimizer/" + module),
                ], ROOT)
                self.assertEqual(chosen, ["src/plugins/epub_optimizer"])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/epub_optimizer/cifereca_epub_optimizer_plugin.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/epub_optimizer/cifereca-plugin.json")],
            [("M", "src/plugins/epub_optimizer/tests/conftest.py")],
            [("D", "src/plugins/epub_optimizer/cifereca_epub_optimizer_plugin.py")],
            [("R100", "src/plugins/epub_optimizer/cifereca_epub_optimizer_plugin.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_epub_optimizer_has_no_other_plugin_implementation_consumers(self):
        tokens = (
            "ui.page.epub-optimizer",
            "document.transformer.epub-optimizer",
            "document.transformer.pdf-eink-optimizer",
            "device.profile.xteink-x3",
            "device.profile.xteink-x4",
            "device.profile.kobo-clara-bw",
            "device.profile.generic-eink",
            "cifereca_epub_optimizer",
            "cifereca_pdf_optimizer",
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "epub_optimizer":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens),
                        f"New EPUB optimizer consumer: {file}",
                    )

    def test_home_page_optional_library_contract_closure(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("home-page", audited)
        self.assertEqual(
            selector.affected_packages({"home-page"}, graph),
            ["home-page"],
        )
        # Home declares an optional provider.library category contract,
        # plus concrete source classification consumers. Providers must
        # retain the reverse closure into Home when they change.
        for provider in (
            "provider-bnf-catalogue", "provider-gallica",
            "provider-gutenberg", "ebooksgratuits",
        ):
            with self.subTest(provider=provider):
                self.assertIn(provider, graph["home-page"])
                self.assertIn(
                    "home-page",
                    selector.affected_packages({provider}, graph),
                )
        for module in ("cifereca_home.py",):
            chosen, reason = selector.select([
                ("M", "src/plugins/home-page/" + module),
            ], ROOT)
            self.assertEqual(chosen, ["src/plugins/home-page"])
            self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/home-page/cifereca_home.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/home-page/cifereca-plugin.json")],
            [("M", "src/plugins/home-page/tests/conftest.py")],
            [("D", "src/plugins/home-page/cifereca_home.py")],
            [("R100", "src/plugins/home-page/cifereca_home.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_home_page_has_no_cross_plugin_implementation_consumers(self):
        tokens = (
            "ui.page.home", "cifereca_home",
            "from cifereca_home import", "import cifereca_home",
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "home-page":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens),
                        f"Unexpected direct home-page plugin consumer: {file}",
                    )

    def test_recommendations_product_is_scoped_to_its_own_suite(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("recommendations", audited)
        self.assertEqual(
            selector.affected_packages({"recommendations"}, graph),
            ["recommendations"],
        )
        # The book-reference recommendation depends on the UI contribution
        # *within the same package*; no external suite is needed.
        for module in (
            "recommendations.py", "recommendation_bridge.py",
            "recommendation_service.py", "recommendation_llm.py",
            "reference_recommendation.py",
        ):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/recommendations/" + module),
                ], ROOT)
                self.assertEqual(chosen, ["src/plugins/recommendations"])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/recommendations/recommendations.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/recommendations/cifereca-plugin.json")],
            [("M", "src/plugins/recommendations/tests/conftest.py")],
            [("D", "src/plugins/recommendations/recommendations.py")],
            [("R100", "src/plugins/recommendations/recommendations.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_recommendations_plugin_ids_and_imports_have_no_other_consumers(self):
        # Generic mentions of "recommendations" in other products are not
        # import edges. Require a concrete plugin ID or Python import.
        tokens = (
            "ui.page.recommendations",
            "recommendation.book-reference.recommendations",
            "from recommendation_service import",
            "from recommendation_bridge import",
            "from recommendation_llm import",
            "from reference_recommendation import",
            "from trace_report import",
            "import recommendation_service",
            "import recommendation_bridge",
            "import reference_recommendation",
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "recommendations":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens),
                        f"New recommendations plugin consumer: {file}",
                    )

    def test_ebooksgratuits_product_selects_home_page_consumer(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("ebooksgratuits", audited)
        self.assertIn("transport-http", graph["ebooksgratuits"])
        self.assertIn("ebooksgratuits", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"ebooksgratuits"}, graph),
            ["ebooksgratuits", "home-page"],
        )
        chosen, reason = selector.select([
            ("M", "src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py"),
        ], ROOT)
        self.assertEqual(chosen, [
            "src/plugins/ebooksgratuits", "src/plugins/home-page",
        ])
        self.assertIn("dependency closure", reason)
        affected = selector.affected_packages({"transport-http"}, graph)
        self.assertTrue(
            {"transport-http", "ebooksgratuits", "home-page"} <= set(affected),
        )
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/ebooksgratuits/cifereca-plugin.json")],
            [("M", "src/plugins/ebooksgratuits/tests/conftest.py")],
            [("D", "src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py")],
            [("R100", "src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_ebooksgratuits_specific_cross_plugin_consumers_are_known(self):
        tokens = (
            "provider.ebooksgratuits", "cifereca_ebooksgratuits",
            "ebooksgratuits",
        )
        source_root = ROOT / "src/plugins"
        consumers = set()
        transport_fixtures = set()
        for package_dir in sorted(source_root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "ebooksgratuits":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    if any(token in data for token in tokens):
                        if package_dir.name == "transport-http":
                            transport_fixtures.add(file.relative_to(package_dir).as_posix())
                        else:
                            consumers.add(package_dir.name)
        # An HTTP transport test exercises a fixed external OPDS URL:
        # it neither imports nor consumes the ebooksgratuits plugin.
        # Any further transport reference must be reviewed explicitly.
        self.assertEqual(transport_fixtures, {
            "tests/test_opds_same_origin_redirect_p1_2460.py",
        })
        self.assertEqual(consumers, {"home-page"})

    def test_gallica_product_selects_home_page_consumer(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-gallica", audited)
        self.assertIn("transport-http", graph["provider-gallica"])
        self.assertIn("provider-gallica", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"provider-gallica"}, graph),
            ["home-page", "provider-gallica"],
        )
        for module in ("cifereca_gallica.py", "cifereca_gallica_audio.py"):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/provider-gallica/" + module),
                ], ROOT)
                self.assertEqual(chosen, [
                    "src/plugins/home-page", "src/plugins/provider-gallica",
                ])
                self.assertIn("dependency closure", reason)
        affected = selector.affected_packages({"transport-http"}, graph)
        self.assertTrue(
            {"transport-http", "provider-gallica", "home-page"} <= set(affected),
        )
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gallica/cifereca_gallica.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gallica/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gallica/tests/conftest.py")],
            [("D", "src/plugins/provider-gallica/cifereca_gallica.py")],
            [("R100", "src/plugins/provider-gallica/cifereca_gallica.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_gallica_plugin_ids_and_imports_have_no_unaudited_consumers(self):
        # The literal "gallica" in BnF XML-link parsing refers to an
        # external URL origin, not an in-process provider.gallica import.
        tokens = (
            "provider.gallica", "provider-gallica",
            "cifereca_gallica", "from cifereca_gallica",
            "import cifereca_gallica",
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "provider-gallica":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens),
                        f"Unexpected Gallica plugin consumer: {file}",
                    )

    def test_gutenberg_product_selects_home_page_consumer(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-gutenberg", audited)
        self.assertIn("transport-http", graph["provider-gutenberg"])
        self.assertIn("provider-gutenberg", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"provider-gutenberg"}, graph),
            ["home-page", "provider-gutenberg"],
        )
        chosen, reason = selector.select([
            ("M", "src/plugins/provider-gutenberg/cifereca_gutenberg.py"),
        ], ROOT)
        self.assertEqual(chosen, [
            "src/plugins/home-page", "src/plugins/provider-gutenberg",
        ])
        self.assertIn("dependency closure", reason)
        # Changing a required transport still reaches Gutenberg, its home
        # consumer, and the other transport dependents transitively.
        affected = selector.affected_packages({"transport-http"}, graph)
        self.assertTrue({"transport-http", "provider-gutenberg", "home-page"} <= set(affected))
        # Home-page is audited independently; reverse dependency is not implied.
        chosen, reason = selector.select([
            ("M", "src/plugins/home-page/cifereca_home.py"),
        ], ROOT)
        self.assertEqual(chosen, ["src/plugins/home-page"])
        self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gutenberg/cifereca_gutenberg.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gutenberg/cifereca-plugin.json")],
            [("M", "src/plugins/provider-gutenberg/tests/conftest.py")],
            [("D", "src/plugins/provider-gutenberg/cifereca_gutenberg.py")],
            [("R100", "src/plugins/provider-gutenberg/cifereca_gutenberg.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_gutenberg_specific_cross_plugin_consumers_are_known(self):
        tokens = (
            "provider-gutenberg", "provider.gutenberg",
            "cifereca_gutenberg", "gutenberg",
        )
        root = ROOT / "src/plugins"
        consumers = set()
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "provider-gutenberg":
                continue
            for file in package_dir.rglob("*"):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh",
                }:
                    data = file.read_text(encoding="utf-8", errors="replace")
                    if any(token in data for token in tokens):
                        consumers.add(package_dir.name)
        self.assertEqual(consumers, {"home-page"})

    def test_bnf_catalogue_product_selects_home_page_consumer(self):
        # home-page hardcodes the public BnF source ID and its non-lending
        # presentation. Its suite must follow any catalogue product change.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-bnf-catalogue", audited)
        self.assertIn("provider-bnf-catalogue", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"provider-bnf-catalogue"}, graph),
            ["home-page", "provider-bnf-catalogue"],
        )
        for module in ("cifereca_bnf_catalogue.py",):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/provider-bnf-catalogue/" + module),
                ], ROOT)
                self.assertEqual(chosen, [
                    "src/plugins/home-page", "src/plugins/provider-bnf-catalogue",
                ])
                self.assertIn("dependency closure", reason)
        # Home-page is now audited; modifying the consumer does not
        # invoke the provider's own suite in the reverse direction.
        chosen, reason = selector.select([
            ("M", "src/plugins/home-page/cifereca_home.py"),
        ], ROOT)
        self.assertEqual(chosen, ["src/plugins/home-page"])
        self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-bnf-catalogue/cifereca-plugin.json")],
            [("M", "src/plugins/provider-bnf-catalogue/tests/conftest.py")],
            [("D", "src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py")],
            [("R100", "src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_bnf_catalogue_specific_cross_package_consumers_are_known(self):
        # Unknown cross-package imports or coupling by ID invalidate this
        # targeted audit. The known home-page consumer has an explicit edge.
        tokens = (
            "provider-bnf-catalogue", "provider.bnf-catalogue",
            "cifereca_bnf_catalogue", "bnf-catalogue", "bnf_catalogue",
        )
        source_root = ROOT / "src/plugins"
        consumers = set()
        for item in sorted(source_root.glob("*/*")):
            package = item.relative_to(source_root).parts[0]
            if package == "provider-bnf-catalogue":
                continue
            sources = item.rglob("*") if item.is_dir() else (item,)
            for file in sources:
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    if any(token in content for token in tokens):
                        consumers.add(package)
        self.assertEqual(consumers, {"home-page"})

    def test_audited_browser_recorder_has_its_own_suite(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("browser-recorder", audited)
        self.assertEqual(
            selector.affected_packages({"browser-recorder"}, graph),
            ["browser-recorder"],
        )
        for module in (
            "browser_recorder.py", "browser_recorder_page.py",
            "browser_recorder_zip.py", "browser_recorder_vault.py",
        ):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/browser-recorder/" + module),
                ], ROOT)
                self.assertEqual(chosen, ["src/plugins/browser-recorder"])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/browser-recorder/browser_recorder.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/browser-recorder/cifereca-plugin.json")],
            [("M", "src/plugins/browser-recorder/tests/conftest.py")],
            [("D", "src/plugins/browser-recorder/browser_recorder.py")],
            [("R100", "src/plugins/browser-recorder/browser_recorder.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_browser_recorder_modules_have_no_other_plugin_consumers(self):
        # Plugin-specific imports and entrypoint, not generic diagnostic
        # "browser_recorder" domain labels used in another package's tests.
        tokens = (
            "from browser_recorder import", "import browser_recorder.",
            "browser_recorder_page", "browser_recorder_devkit",
            "browser_recorder_zip", "browser_recorder_review",
            "browser_recorder_vault", "browser_recorder_http",
            "browser_recorder_download", "browser_recorder_review_ui",
            "ui.page.browser-recorder", "org.cifereca.technical.browser-recorder",
        )
        source_root = ROOT / "src/plugins"
        for item in sorted(source_root.glob("*/*")):
            package = item.relative_to(source_root).parts[0]
            if package == "browser-recorder":
                continue
            sources = item.rglob("*") if item.is_dir() else (item,)
            for file in sources:
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New cross-plugin Browser Recorder consumer: {file}",
                    )

    def test_audited_diagnostics_page_is_a_single_plugin_suite(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("diagnostics-page", audited)
        self.assertEqual(
            selector.affected_packages({"diagnostics-page"}, graph),
            ["diagnostics-page"],
        )
        for module in (
            "cifereca_diagnostics_page.py", "diagnostics_explorer.py",
            "diagnostics_ai.py", "diagnostics_support.py",
        ):
            with self.subTest(module=module):
                targets, reason = selector.select([
                    ("M", "src/plugins/diagnostics-page/" + module),
                ], ROOT)
                self.assertEqual(targets, ["src/plugins/diagnostics-page"])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/diagnostics-page/diagnostics_ai.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/diagnostics-page/cifereca-plugin.json")],
            [("M", "src/plugins/diagnostics-page/tests/conftest.py")],
            [("D", "src/plugins/diagnostics-page/diagnostics_ai.py")],
            [("R100", "src/plugins/diagnostics-page/diagnostics_ai.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_diagnostics_page_plugin_has_no_cross_package_consumers(self):
        # Concrete modules and contribution IDs, not generic "diagnostics".
        tokens = (
            "diagnostics-page", "cifereca_diagnostics_page",
            "diagnostics_explorer", "diagnostics_ai",
            "diagnostics_support", "diagnostics_selection",
            "diagnostics_performance", "diagnostics_pseudonyms",
            "diagnostics_adept_guidance", "ui.page.diagnostics",
            "org.cifereca.diagnostics",
        )
        source_root = ROOT / "src/plugins"
        for item in sorted(source_root.glob("*/*")):
            package = item.relative_to(source_root).parts[0]
            if package == "diagnostics-page":
                continue
            sources = item.rglob("*") if item.is_dir() else (item,)
            for file in sources:
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New diagnostics-page plugin consumer: {file}",
                    )

    def test_audited_constellations_only_targets_its_plugin_suite(self):
        # navigation.constellation and ui.page have no external contract
        # consumers in plugin manifests; all dependencies are Core or local.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("constellations", audited)
        self.assertEqual(
            selector.affected_packages({"constellations"}, graph),
            ["constellations"],
        )
        for module in ("constellations.py", "constellations_page.py",
                       "constellation_view_model.py", "semantic_llm.py"):
            with self.subTest(module=module):
                chosen, reason = selector.select([
                    ("M", "src/plugins/constellations/" + module),
                ], ROOT)
                self.assertEqual(chosen, ["src/plugins/constellations"])
                self.assertIn("dependency closure", reason)
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/constellations/constellations.py"),
             ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/constellations/cifereca-plugin.json")],
            [("M", "src/plugins/constellations/tests/helpers.py")],
            [("D", "src/plugins/constellations/constellations_page.py")],
            [("R100", "src/plugins/constellations/constellations.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_audited_constellations_has_no_other_plugin_consumers(self):
        # Ignore a prose comparison in recommendations: only a real module,
        # plugin ID, manifest or path reference creates this dependency.
        tokens = (
            "constellations_page", "constellation_view_model",
            "navigation.constellations", "navigation.constellation",
            "ui.page.constellations", "org.cifereca.constellations",
            "from constellations import", "import constellations",
            "from semantic_llm import", "/constellations/",
        )
        source_root = ROOT / "src/plugins"
        for item in sorted(source_root.glob("*/*")):
            package = item.relative_to(source_root).parts[0]
            if package == "constellations":
                continue
            sources = item.rglob("*") if item.is_dir() else (item,)
            for file in sources:
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New constellations plugin consumer: {file}",
                    )

    def test_reference_provider_product_includes_home_page_consumer(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-reference", audited)
        self.assertIn("provider-reference", graph["home-page"])
        for module in ("cifereca_reference.py", "cifereca_reference_fixtures.py"):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/provider-reference/{module}"),
                ], ROOT)
                self.assertEqual(selected, [
                    "src/plugins/home-page", "src/plugins/provider-reference",
                ])
                self.assertIn("dependency closure", reason)

    def test_reference_provider_keeps_exhaustive_fallbacks(self):
        product = "src/plugins/provider-reference/cifereca_reference.py"
        for changes in (
            [("M", product), ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-reference/cifereca-plugin.json")],
            [("M", "src/plugins/provider-reference/tests/conftest.py")],
            [("D", product)],
            [("R100", product)],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_reference_provider_has_no_other_plugin_consumers(self):
        tokens = ("provider-reference", "provider.reference", "cifereca_reference")
        source_root = ROOT / "src/plugins"
        for package in sorted(source_root.iterdir()):
            if not package.is_dir() or package.name == "provider-reference":
                continue
            for file in sorted(package.rglob("*")):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New explicit reference provider consumer: {file}",
                    )

    def test_audited_transport_http_includes_every_declared_consumer(self):
        # A shared HTTP transport is NEVER an isolated one-suite change.
        # Manifest requires: Baen, Gutenberg, Gallica, EbooksGratuits.
        # EbooksGratuits/Gallica/Gutenberg propagate to Home through
        # home-page's provider.library contract and the explicit graph.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("transport-http", audited)
        expected = [
            "baen", "ebooksgratuits", "home-page",
            "provider-gallica", "provider-gutenberg", "transport-http",
        ]
        self.assertEqual(
            selector.affected_packages({"transport-http"}, graph),
            expected,
        )
        for module in (
            "cifereca_http_transport.py", "cifereca_http_transport_health.py",
        ):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/transport-http/{module}"),
                ], ROOT)
                self.assertEqual(
                    selected, [f"src/plugins/{package}" for package in expected],
                )
                self.assertIn("dependency closure", reason)

    def test_transport_http_unaudited_mixed_changes_remain_full(self):
        product = "src/plugins/transport-http/cifereca_http_transport.py"
        for changes in (
            [("M", product), ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/transport-http/cifereca-plugin.json")],
            [("M", "src/plugins/transport-http/tests/conftest.py")],
            [("D", product)],
            [("R100", product)],
            [("M", product), ("M", "src/cifereca/services.py")],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_transport_http_declared_consumers_stay_in_graph(self):
        # An accidental removal of a declared required edge must not
        # silently reduce the coverage of this shared security boundary.
        graph, _ = selector.dependency_graph(ROOT)
        consumers = {"baen", "ebooksgratuits",
                     "provider-gallica", "provider-gutenberg"}
        for consumer in consumers:
            self.assertIn("transport-http", graph[consumer])
        self.assertTrue(
            {"ebooksgratuits", "provider-gallica",
             "provider-gutenberg"} <= graph["home-page"]
        )

    def test_audited_numilog_private_and_public_depend_on_home_page(self):
        # One manifest/package contains private owned Numilog and its public
        # anonymous commercial catalogue; neither is a separate test target.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-numilog", audited)
        self.assertIn("provider-numilog", graph["home-page"])
        self.assertEqual(
            selector.affected_packages({"provider-numilog"}, graph),
            ["home-page", "provider-numilog"],
        )
        for module in (
            "cifereca_numilog.py",
            "cifereca_numilog_public_provider.py",
            "cifereca_numilog_public_metadata.py",
            "cifereca_numilog_enrichment.py",
        ):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/provider-numilog/{module}"),
                ], ROOT)
                self.assertEqual(
                    selected,
                    ["src/plugins/home-page", "src/plugins/provider-numilog"],
                )
                self.assertIn("dependency closure", reason)

    def test_numilog_manifest_and_unqualified_changes_still_force_full(self):
        product = "src/plugins/provider-numilog/cifereca_numilog.py"
        for changes in (
            [("M", product), ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-numilog/cifereca-plugin.json")],
            [("M", "src/plugins/provider-numilog/tests/conftest.py")],
            [("D", product)],
            [("R100", product)],
            [("M", product), ("M", "src/cifereca/services.py")],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_numilog_has_no_cross_package_plugin_import_consumers(self):
        # Check concrete module imports and the two declared plugin IDs,
        # not generic lower-case text such as Numilog in UIs or diagnostics.
        tokens = (
            "from cifereca_numilog", "import cifereca_numilog",
            "provider.numilog", "org.cifereca.provider.numilog",
            "/provider-numilog/", "src/plugins/provider-numilog",
        )
        source_root = ROOT / "src/plugins"
        for package in sorted(source_root.iterdir()):
            if not package.is_dir() or package.name == "provider-numilog":
                continue
            for file in sorted(package.rglob("*")):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New explicit Numilog plugin consumer: {file}",
                    )

    def test_audited_paris_provider_closes_to_home(self):
        # Only the manifest-declared provider.library consumer is exercised
        # by this audit; private Core acquisition/DRM code is not re-scoped.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-paris", audited)
        self.assertIn("provider-paris", graph["home-page"])
        self.assertEqual(selector.affected_packages({"provider-paris"}, graph),
                         ["home-page", "provider-paris"])
        for module in ("cifereca_paris.py", "cifereca_paris_borrow.py",
                       "cifereca_paris_client.py",
                       "cifereca_paris_normalize.py"):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/provider-paris/{module}"),
                ], ROOT)
                self.assertEqual(
                    selected,
                    ["src/plugins/home-page", "src/plugins/provider-paris"],
                )
                self.assertIn("dependency closure", reason)

    def test_paris_unaudited_mixed_and_shared_changes_force_full(self):
        product = "src/plugins/provider-paris/cifereca_paris_borrow.py"
        for changes in (
            [("M", product), ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-paris/cifereca-plugin.json")],
            [("M", "src/plugins/provider-paris/tests/conftest.py")],
            [("D", product)],
            [("R100", product)],
            [("M", product), ("M", "src/cifereca/services.py")],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_paris_specific_module_imports_have_no_plugin_consumers(self):
        # Exclude generic diagnostic fixture labels ('provider.paris') since
        # they only prove inventory rendering, not a Paris code import.
        tokens = ("from cifereca_paris", "import cifereca_paris",
                  "src/plugins/provider-paris/", "/provider-paris/")
        source_root = ROOT / "src/plugins"
        for package in sorted(source_root.iterdir()):
            if not package.is_dir() or package.name == "provider-paris":
                continue
            for file in sorted(package.rglob("*")):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New direct Paris plugin module consumer: {file}",
                    )

    def test_audited_bnf_pret_closes_to_home_page(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-bnf-pret", audited)
        self.assertIn("provider-bnf-pret", graph["home-page"])
        self.assertEqual(selector.affected_packages({"provider-bnf-pret"}, graph),
                         ["home-page", "provider-bnf-pret"])
        for module in (
            "cifereca_bnf_pret.py", "cifereca_bnf_pret_borrow.py",
            "cifereca_bnf_pret_signed_action.py",
            "cifereca_bnf_pret_loan_selection.py",
            "cifereca_bnf_pret_http.py", "cifereca_bnf_pret_catalog.py",
        ):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/provider-bnf-pret/{module}"),
                ], ROOT)
                self.assertEqual(
                    selected,
                    ["src/plugins/home-page", "src/plugins/provider-bnf-pret"],
                )
                self.assertIn("dependency closure", reason)

    def test_bnf_pret_unsafe_changes_keep_full_suite(self):
        product = "src/plugins/provider-bnf-pret/cifereca_bnf_pret_borrow.py"
        for changes in (
            [("M", product), ("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-bnf-pret/cifereca-plugin.json")],
            [("M", "src/plugins/provider-bnf-pret/tests/conftest.py")],
            [("D", product)], [("R100", product)],
            [("M", product), ("M", "src/cifereca/services.py")],
        ):
            with self.subTest(changes=changes):
                chosen, reason = selector.select(changes, ROOT)
                self.assertEqual(chosen, [])
                self.assertTrue(reason.startswith("full:"), reason)

    def test_bnf_pret_specific_modules_have_no_cross_plugin_consumers(self):
        tokens = ("from cifereca_bnf_pret", "import cifereca_bnf_pret",
                  "src/plugins/provider-bnf-pret/", "/provider-bnf-pret/")
        source_root = ROOT / "src/plugins"
        for package in sorted(source_root.iterdir()):
            if not package.is_dir() or package.name == "provider-bnf-pret":
                continue
            for file in sorted(package.rglob("*")):
                if file.is_file() and file.suffix in {
                    ".py", ".json", ".toml", ".yml", ".yaml", ".sh",
                }:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in content for token in tokens),
                        f"New direct BnF Prêt plugin consumer: {file}",
                    )

    def test_kobo_account_and_catalogue_share_home_closure(self):
        graph, audited = selector.dependency_graph(ROOT)
        self.assertIn("provider-kobo", audited)
        self.assertIn("provider-kobo", graph["home-page"])
        # Kobo consumes the separately audited ADEPT runtime through its
        # negotiated Core broker; do NOT conflate plugin IDs drm.ade and
        # runtime.kobo-adept or select runtime when only Kobo changes.
        self.assertIn("runtime-kobo-adept", graph["provider-kobo"])
        self.assertEqual(
            selector.affected_packages({"provider-kobo"}, graph),
            ["home-page", "provider-kobo"],
        )
        for module in ("cifereca_kobo.py", "cifereca_kobo_catalog.py",
                       "cifereca_kobo_delivery_offer.py"):
            with self.subTest(module=module):
                selected, reason = selector.select([
                    ("M", f"src/plugins/provider-kobo/{module}"),
                ], ROOT)
                self.assertEqual(
                    selected,
                    ["src/plugins/home-page", "src/plugins/provider-kobo"],
                )
                self.assertIn("dependency closure", reason)
        # Runtime modification still tests the full 6-suite consumer closure.
        runtime = selector.affected_packages({"runtime-kobo-adept"}, graph)
        self.assertEqual(runtime, [
            "diagnostics-page", "home-page", "provider-kobo",
            "provider-numilog", "provider-paris", "runtime-kobo-adept",
        ])

    def test_kobo_manifest_shared_and_unsafe_diff_remain_full(self):
        product = "src/plugins/provider-kobo/cifereca_kobo.py"
        for changes in (
            [("M", "src/plugins/provider-kobo/cifereca-plugin.json")],
            [("M", "src/plugins/provider-kobo/tests/conftest.py")],
            [("D", product)], [("R100", product)],
            [("M", product), ("M", "src/cifereca/services.py")],
            [("M", product), ("M", "src/plugins/runtime-kobo-adept/cifereca-plugin.json")],
        ):
            with self.subTest(changes=changes):
                selected, reason = selector.select(changes, ROOT)
                self.assertEqual(selected, [])
                self.assertTrue(reason.startswith("full:"), reason)
        # Two audited product packages can be selected together, rather
        # than silently dropped or declared full without cause.
        selected, reason = selector.select([
            ("M", product),
            ("M", "src/plugins/provider-paris/cifereca_paris.py"),
        ], ROOT)
        self.assertEqual(selected, [
            "src/plugins/home-page", "src/plugins/provider-kobo",
            "src/plugins/provider-paris",
        ])
        self.assertIn("dependency closure", reason)

    def test_kobo_no_undeclared_cross_package_python_imports(self):
        tokens = ("provider.kobo.catalog", "org.cifereca.provider.kobo")
        # The distinct ADEPT runtime's own modules are named
        # cifereca_kobo_adept_*. They are NOT imports of Kobo provider code.
        provider_import = re.compile(
            r"(?m)^\s*(?:from|import)\s+cifereca_kobo"
            r"(?:\b|_(?!adept(?:_|\b))|\.)"
        )
        root = ROOT / "src/plugins"
        for package_dir in sorted(root.iterdir()):
            if not package_dir.is_dir() or package_dir.name == "provider-kobo":
                continue
            for file in sorted(package_dir.rglob("*")):
                if file.is_file() and file.suffix in (
                    ".py", ".json", ".toml", ".yaml", ".yml", ".sh"
                ):
                    data = file.read_text(encoding="utf-8", errors="replace")
                    self.assertFalse(
                        any(token in data for token in tokens)
                        or provider_import.search(data) is not None,
                        f"New Kobo provider plugin consumer: {file}",
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
