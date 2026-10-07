from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.plugin_packaging import (
    PackagingError,
    build_all_in_one,
    build_dev_snapshot,
    build_plugin_zip,
    discover_plugins,
    validate_dev_snapshot,
    validate_plugin_entrypoints,
    validate_plugin_source,
    validate_plugin_zip,
    verify_repository,
)


def make_plugin(root: Path, name: str, package_id: str, version: str) -> Path:
    directory = root / name
    directory.mkdir()
    manifest = {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": version,
            "name": name,
            "publisher": "tests",
            "plugin_api": ">=1.3,<2",
        },
        "plugins": [
            {
                "id": f"{name}.service",
                "type": "provider.library",
                "entrypoint": "plugin:PLUGIN",
                "version": version,
            }
        ],
    }
    (directory / "cifereca-plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (directory / "plugin.py").write_text("PLUGIN = object()\n", encoding="utf-8")
    return directory


class PluginPackagingTests(unittest.TestCase):
    def test_plugin_zip_has_manifest_at_root_and_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.2.3"
            )
            one = build_plugin_zip(plugin, root / "one")
            two = build_plugin_zip(plugin, root / "two")
            self.assertEqual(
                hashlib.sha256(one.read_bytes()).hexdigest(),
                hashlib.sha256(two.read_bytes()).hexdigest(),
            )
            with zipfile.ZipFile(one) as archive:
                self.assertIn("cifereca-plugin.json", archive.namelist())
                self.assertIn("plugin.py", archive.namelist())
                self.assertFalse(
                    any(name.startswith("alpha/") for name in archive.namelist())
                )

    def test_plugin_zip_reuses_full_manifest_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad-plugin.zip"
            manifest = {
                "manifest": 1,
                "package": {
                    "id": "org.cifereca.provider.alpha",
                    "version": "1.0",
                    "plugin_api": ">=99",
                },
                "plugins": [
                    {
                        "id": "alpha.service",
                        "type": "provider.library",
                        "entrypoint": "plugin:PLUGIN",
                        "version": "1.0",
                    }
                ],
            }
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("cifereca-plugin.json", json.dumps(manifest))
                output.writestr("plugin.py", "PLUGIN = object()\n")

            with self.assertRaisesRegex(
                PackagingError, r"package\.plugin_api incompatible"
            ):
                validate_plugin_zip(archive)

    def test_all_in_one_contains_individual_plugin_zips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "2.0"
            )
            output = build_all_in_one(
                discover_plugins(plugins),
                root / "dist",
                filename="all.zip",
            )
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                bundle = json.loads(archive.read("cifereca-plugins.json"))
            self.assertEqual(len(bundle["plugins"]), 2)
            self.assertEqual(
                len(
                    [
                        name
                        for name in names
                        if name.startswith("packages/") and name.endswith(".zip")
                    ]
                ),
                2,
            )
            self.assertIn("SHA256SUMS", names)

    def test_snapshot_matches_core044_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "2.0"
            )
            output = build_dev_snapshot(
                discover_plugins(plugins),
                root / "dist",
                snapshot_id="cifereca-dev-test",
            )
            manifest = validate_dev_snapshot(output)
            self.assertEqual(manifest["format"], 1)
            self.assertEqual(manifest["kind"], "cifereca-dev-snapshot")
            self.assertEqual(manifest["id"], "cifereca-dev-test")
            self.assertEqual(len(manifest["plugins"]), 2)
            self.assertTrue(all(row["activate"] for row in manifest["plugins"]))
            with zipfile.ZipFile(output) as archive:
                roots = {name.split("/", 1)[0] for name in archive.namelist()}
                self.assertEqual(roots, {"cifereca-dev-test"})
                self.assertIn(
                    "cifereca-dev-test/cifereca-dev.json",
                    archive.namelist(),
                )
                for row in manifest["plugins"]:
                    member = f"cifereca-dev-test/{row['path']}"
                    payload = archive.read(member)
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(),
                        row["sha256"],
                    )

    def test_entrypoint_module_file_is_validated_without_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest = json.loads(
                (plugin / "cifereca-plugin.json").read_text(encoding="utf-8")
            )
            validate_plugin_entrypoints(plugin, manifest)

    def test_entrypoint_python_package_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["entrypoint"] = "alpha_plugin.runtime:PLUGIN"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            package = plugin / "alpha_plugin" / "runtime"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "raise RuntimeError('must not be imported while packaging')\n"
                "PLUGIN = object()\n",
                encoding="utf-8",
            )
            validate_plugin_entrypoints(plugin, manifest)

    def test_entrypoint_cannot_live_in_development_only_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for dirname in ("tests", "simulation"):
                plugin = make_plugin(
                    root, f"alpha-{dirname}", f"org.cifereca.{dirname}", "1.0"
                )
                manifest_path = plugin / "cifereca-plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["plugins"][0]["entrypoint"] = f"{dirname}.fake:PLUGIN"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                directory = plugin / dirname
                directory.mkdir()
                (directory / "fake.py").write_text(
                    "PLUGIN = object()\n", encoding="utf-8"
                )

                with self.assertRaisesRegex(
                    PackagingError, r"entrypoint hors production"
                ):
                    build_plugin_zip(plugin, root / f"dist-{dirname}")

    def test_packaging_refuses_missing_entrypoint_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["entrypoint"] = "missing.module:PLUGIN"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                PackagingError,
                r"module entrypoint absent.*missing\.module",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_entrypoint_must_use_module_colon_attribute_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest = json.loads(
                (plugin / "cifereca-plugin.json").read_text(encoding="utf-8")
            )
            manifest["plugins"][0]["entrypoint"] = "plugin.PLUGIN"
            with self.assertRaisesRegex(PackagingError, "entrypoint invalide"):
                validate_plugin_entrypoints(plugin, manifest)

    def test_production_plugin_may_only_import_public_plugin_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            (plugin / "plugin.py").write_text(
                "from cifereca.plugin_api import ProviderError\n"
                "from cifereca.services import LibraryService\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PackagingError,
                r"plugin\.py:2: cifereca\.services",
            ):
                validate_plugin_source(plugin)

    def test_public_plugin_api_submodule_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            (plugin / "plugin.py").write_text(
                "import cifereca.plugin_api.models\n"
                "from cifereca.plugin_api import ProviderError\n",
                encoding="utf-8",
            )
            validate_plugin_source(plugin)

    def test_tests_and_simulation_are_not_production_boundary_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            for dirname in ("tests", "simulation"):
                directory = plugin / dirname
                directory.mkdir()
                (directory / "helper.py").write_text(
                    "from cifereca.services import LibraryService\n",
                    encoding="utf-8",
                )
            validate_plugin_source(plugin)

    def test_tests_and_simulation_are_excluded_from_production_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            for dirname in ("tests", "simulation"):
                directory = plugin / dirname
                directory.mkdir()
                (directory / "fixture.txt").write_text(
                    "development-only\n", encoding="utf-8"
                )

            output = build_plugin_zip(plugin, root / "dist")
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()

            self.assertIn("plugin.py", names)
            self.assertFalse(any(name.startswith("tests/") for name in names))
            self.assertFalse(any(name.startswith("simulation/") for name in names))

    def test_packaging_refuses_private_core_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            (plugin / "plugin.py").write_text(
                "import cifereca.storage\nPLUGIN = object()\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PackagingError, "cifereca.storage"):
                build_plugin_zip(plugin, root / "dist")

    def test_all_in_one_refuses_duplicate_plugin_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            first = make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            second = make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "1.0"
            )
            first_manifest = json.loads(
                (first / "cifereca-plugin.json").read_text(encoding="utf-8")
            )
            second_manifest_path = second / "cifereca-plugin.json"
            second_manifest = json.loads(
                second_manifest_path.read_text(encoding="utf-8")
            )
            second_manifest["plugins"][0]["id"] = first_manifest["plugins"][0]["id"]
            second_manifest_path.write_text(
                json.dumps(second_manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(PackagingError, r"plugin\.id dupliqué"):
                build_all_in_one(
                    discover_plugins(plugins),
                    root / "dist",
                    filename="all.zip",
                )

    def test_verify_repository_refuses_duplicate_plugin_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            first = make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            second = make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "1.0"
            )
            first_manifest = json.loads(
                (first / "cifereca-plugin.json").read_text(encoding="utf-8")
            )
            second_manifest_path = second / "cifereca-plugin.json"
            second_manifest = json.loads(
                second_manifest_path.read_text(encoding="utf-8")
            )
            second_manifest["plugins"][0]["id"] = first_manifest["plugins"][0]["id"]
            second_manifest_path.write_text(
                json.dumps(second_manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(PackagingError, r"plugin\.id dupliqué"):
                verify_repository(plugins)

    def test_packaging_refuses_noncanonical_plugin_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["id"] = "Provider.Alpha"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.id invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_duplicate_plugin_id_in_same_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            duplicate = dict(manifest["plugins"][0])
            duplicate["entrypoint"] = "plugin:PLUGIN"
            manifest["plugins"].append(duplicate)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugin\.id dupliqué dans le package"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_noncanonical_dependency_plugin_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires"] = [
                {"plugin": "Provider.Beta", "version": ">=1"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires\[0\]\.plugin invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_noncanonical_contract_category_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {"category": "Library.Read", "contract": "library.read"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires_contracts\[0\]\.category invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_dependency_field_that_is_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires"] = "provider.beta"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.requires doit être une liste"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_dependency_entry_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires"] = [42]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires\[0\].*chaîne ou un objet"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_categories_that_are_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["categories"] = {"id": "provider.library"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"categories doit être une liste"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_contract_requirements_that_are_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = {
                "category": "provider.library",
                "contract": "provider.library",
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires_contracts doit être une liste"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_unknown_system_requirement_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["system_requires"] = [{"type": "daemon"}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"system_requires\[0\]\.type invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_requires_name_for_python_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["system_requires"] = [
                {"type": "python-package"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"name requis pour python-package"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_system_requirement_version_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["system_requires"] = [
                {"type": "binary", "name": "tool", "version_args": [42]}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"version_args doit être une liste de chaînes"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_category_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["categories"] = [
                {"id": "provider.library", "resolution": "random"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"categories\[0\]\.resolution invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_truthy_non_object_config_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["config_schema"] = ["bad"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.config_schema doit être un objet"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_accepts_falsy_non_object_config_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["config_schema"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = build_plugin_zip(plugin, root / "dist")
            self.assertTrue(output.exists())

    def test_packaging_refuses_invalid_instantiation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["instantiation"] = "global"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.instantiation invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_accepts_singleton_instantiation_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["instantiation"] = "singleton"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = build_plugin_zip(plugin, root / "dist")
            self.assertTrue(output.is_file())

    def test_packaging_refuses_non_string_help_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["help_text"] = ["bad"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.help_text doit être une chaîne"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_non_list_requires_brokers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_brokers"] = "catalog"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError,
                r"plugins\[0\]\.requires_brokers.*liste de chaînes non vides",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_empty_requires_services_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_services"] = ["library", " "]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError,
                r"plugins\[0\]\.requires_services.*liste de chaînes non vides",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_plugin_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["capabilities"] = ["catalog.read", ""]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError,
                r"plugins\[0\]\.capabilities.*liste de chaînes non vides",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_contract_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {
                    "category": "provider.library",
                    "contract": "provider.library",
                    "capabilities": "catalog.read",
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError,
                r"requires_contracts\[0\]\.capabilities.*liste de chaînes non vides",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_requires_contract_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {"category": "provider.library"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires_contracts\[0\]\.contract est requis"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_contract_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {
                    "category": "provider.library",
                    "contract": "provider.library",
                    "mode": "best-effort",
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires_contracts\[0\]\.mode invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_non_integer_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["priority"] = "high"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"plugins\[0\]\.priority invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_incompatible_plugin_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["package"]["plugin_api"] = ">=99"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"package\.plugin_api incompatible"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_noncanonical_plugin_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["capabilities"] = ["Catalog.Read"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"capabilities\[0\].*non canonique"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_noncanonical_contract_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {
                    "category": "provider.library",
                    "contract": "cifereca.provider",
                    "capabilities": ["catalog"],
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError,
                r"requires_contracts\[0\]\.capabilities\[0\].*non canonique",
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_embedded_contract_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires_contracts"] = [
                {
                    "category": "provider.library",
                    "contract": "provider.library/not-a-version",
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires_contracts\[0\]\.version invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_accepts_numeric_package_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["package"]["version"] = 1.2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = build_plugin_zip(plugin, root / "dist")
            self.assertTrue(output.exists())

    def test_packaging_accepts_string_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifest"] = "1"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = build_plugin_zip(plugin, root / "dist")
            self.assertTrue(output.exists())

    def test_packaging_refuses_invalid_package_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["package"]["version"] = "not a version"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackagingError, r"package\.version invalide"):
                build_plugin_zip(plugin, root / "dist")

    def test_packaging_refuses_invalid_dependency_specifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            manifest_path = plugin / "cifereca-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugins"][0]["requires"] = [
                {"plugin": "provider.beta", "version": "=>1.0"}
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PackagingError, r"requires\[0\]\.version invalide"
            ):
                build_plugin_zip(plugin, root / "dist")

    def test_real_repository_discovers_all_packages(self) -> None:
        plugins = Path(__file__).resolve().parents[1] / "src" / "plugins"
        expected = sorted(path.parent.resolve() for path in plugins.glob("*/cifereca-plugin.json"))
        self.assertEqual(discover_plugins(plugins), expected)
        self.assertGreater(len(expected), 0)


if __name__ == "__main__":
    unittest.main()
