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
        # The three provider manifests recommend drm.ade, while the
        # in-repository ADEPT package declares runtime.kobo-adept instead.
        # Treating those distinct plugin IDs as aliases would be unsound.
        # The runtime package remains UNAUDITED: changing it still selects
        # the full plugin test suite, covering the optional consumers.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertNotIn("runtime-kobo-adept", audited)
        self.assertFalse({
            "provider-kobo", "provider-paris", "provider-numilog",
        } & set(selector.affected_packages({"runtime-kobo-adept"}, graph)))
        names, reason = selector.select([
            ("M", "src/plugins/runtime-kobo-adept/cifereca_kobo_adept.py"),
        ], ROOT)
        self.assertEqual(names, [])
        self.assertIn("not audited", reason)

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
        self.assertEqual(audited, {"browser-recorder", "constellations", "dev-updater", "diagnostics-page", "ebooksgratuits", "literary-criticism", "maintenance-library-audit", "provider-bnf-catalogue", "provider-gallica", "provider-gutenberg"})
        self.assertFalse(any(name.startswith("youth-") for name in graph))
        self.assertTrue({"transport-http", "baen", "ebooksgratuits"} <= set(graph))

    def test_audited_literary_criticism_stays_isolated_from_other_plugin_suites(self):
        # Specific package proved independent. Any newly discovered consumer
        # must trigger an explicit graph audit before maintaining this scope.
        graph, audited = selector.dependency_graph(ROOT)
        self.assertEqual(audited, {"browser-recorder", "constellations", "dev-updater", "diagnostics-page", "ebooksgratuits", "literary-criticism", "maintenance-library-audit", "provider-bnf-catalogue", "provider-gallica", "provider-gutenberg"})
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
        # All product packages not audited retain exhaustive selection.
        for changes in (
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/maintenance-library-audit/cifereca_library_audits.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
            [("M", "src/plugins/home-page/cifereca_home.py")],
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
            [("M", "src/plugins/home-page/cifereca_home.py")],
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/provider-gallica/cifereca_gallica.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
        # A modification to the unaudited home-page itself stays exhaustive.
        chosen, reason = selector.select([
            ("M", "src/plugins/home-page/cifereca_home.py"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("not audited", reason)
        for changes in (
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/provider-gutenberg/cifereca_gutenberg.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
        # The reverse direction is not implied; home-page itself remains
        # unaudited and its product changes run the full plugin gate.
        chosen, reason = selector.select([
            ("M", "src/plugins/home-page/cifereca_home.py"),
        ], ROOT)
        self.assertEqual(chosen, [])
        self.assertIn("not audited", reason)
        for changes in (
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/browser-recorder/browser_recorder.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/diagnostics-page/diagnostics_ai.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
            [("M", "src/plugins/baen/cifereca_baen.py")],
            [("M", "src/plugins/constellations/constellations.py"),
             ("M", "src/plugins/baen/cifereca_baen.py")],
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
