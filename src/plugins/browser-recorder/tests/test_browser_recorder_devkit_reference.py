"""A Core plugin generated exclusively from the reviewed ZIP's kit works.

This is a deterministic *reference reconstruction*, not a claim that an
independent LLM has generated the correct plugin with no repository access.
The fixture itself uses no real provider, account, borrow, download or network.
"""
from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from browser_recorder_devkit import core_contract_kit
from browser_recorder_http import PrivateHttpTrace
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip
from cifereca.plugin_api import (
    CatalogServiceFactory, ItemDetail, NotFound, PLUGIN_API_VERSION,
    SearchResult, UnsupportedOperation, check_service_contract,
)

PREFIX = "plugin-dev-kit/examples/provider-fixture-example/"


def test_zip_only_reference_project_packages_and_passes_public_contract(tmp_path):
    raw = PrivateHttpTrace(("https://fixture-books.example.org",))
    review = IdentifierReview(raw)
    blob = prepare_reviewed_zip(review)._blob
    destination = tmp_path / "provider-fixture-example"
    with ZipFile(BytesIO(blob)) as archive:
        names = archive.namelist()
        assert all(not n.startswith("/") and ".." not in Path(n).parts
                   for n in names)
        chosen = [n for n in names if n.startswith(PREFIX)]
        assert len(chosen) == 4
        for path in chosen:
            target = destination / path[len(PREFIX):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(path))
    assert (destination / "tests" / "test_fixture_catalog.py").exists()
    parsed = json.loads((destination / "cifereca-plugin.json").read_text())
    assert parsed["package"]["plugin_api"] == ">=1.4,<2"
    assert PLUGIN_API_VERSION.startswith("1.")
    assert parsed["plugins"][0]["type"] == "catalog.service"
    assert parsed["plugins"][0]["capabilities"] == [
        "catalog.search", "catalog.detail",
    ]
    from cifereca.plugins.manifest import load_manifest
    descriptor = load_manifest(destination / "cifereca-plugin.json")
    assert descriptor.plugins[0].id == "catalog.fixture-example"
    # Plugin tests are intentionally run with plugin root as cwd; the
    # repository's *official* packager is located via the known test path.
    # This does not supply any missing source file to the extracted example.
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from scripts.plugin_packaging import build_plugin_zip, validate_plugin_zip
    bundle = build_plugin_zip(destination, tmp_path / "output")
    validated = validate_plugin_zip(bundle)
    assert validated["package"]["id"] == parsed["package"]["id"]
    assert validated["package"]["version"] == "0.1.0"
    with ZipFile(bundle) as archive:
        assert "fixture_catalog.py" in archive.namelist()
        assert "tests/test_fixture_catalog.py" not in archive.namelist()
    module_file = destination / "fixture_catalog.py"
    spec = importlib.util.spec_from_file_location(
        "fixture_catalog", module_file,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module.PLUGIN, CatalogServiceFactory)
    assert check_service_contract("catalog.service", module.PLUGIN).ok
    catalog = module.PLUGIN.create()
    result = catalog.search("Fictional", limit=1, page=2)
    assert len(result) == 1
    assert isinstance(result[0], SearchResult)
    assert result[0].record_id == "book-215"
    assert result[0].owned is None
    assert result[0].downloadable is None
    assert isinstance(catalog.detail("book-214"), ItemDetail)
    assert catalog.detail("book-214").availability_status == "unknown"
    assert catalog.search("Fictional", limit=1, page=3) == []
    with pytest.raises(UnsupportedOperation):
        catalog.search("Fictional", field="borrow")
    with pytest.raises(NotFound):
        catalog.detail("not-recorded")
    assert not any(hasattr(catalog, verb) for verb in (
        "borrow", "return_loan", "download", "authenticate",
    ))
    raw.destroy()


def test_example_manifest_matches_actual_public_contract():
    from browser_recorder_devkit_templates import (
        reference_plugin_files, REFERENCE_MANIFEST,
    )
    from cifereca.plugin_api import contract_definition
    from cifereca.plugins.manifest import parse_manifest
    contract = contract_definition("catalog.service")
    assert contract.contract_id == "cifereca.catalog.service"
    assert contract.version == "1"
    parsed = parse_manifest(REFERENCE_MANIFEST)
    assert parsed.plugins[0].type == "catalog.service"
    assert parsed.plugins[0].enabled_by_default is False
    kit = core_contract_kit()
    for name in reference_plugin_files():
        assert name in kit
    manifest = json.loads(kit["plugin-dev-kit/manifest.json"])
    declared = {
        row["path"] for row in manifest["files"]
    }
    assert set(reference_plugin_files()).issubset(declared)


def test_reference_plugin_pytest_passes_without_browser_or_github(tmp_path):
    kit = core_contract_kit()
    destination = tmp_path / "offline-fixture"
    for name, data in kit.items():
        if name.startswith(PREFIX):
            file_path = destination / name[len(PREFIX):]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
    # The copied project is the *only* fixture plugin code visible to pytest.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(destination), env.get("PYTHONPATH", ""),
    ))
    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(destination / "tests")],
        cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 0, (
        "Offline fixture tests failed (only pytest status shown)"
    )
    assert "3 passed" in child.stdout
