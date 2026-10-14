from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EPUB = ROOT / "src/cifereca/epub_metadata.py"
CATALOG = ROOT / "src/cifereca/catalog.py"
CLI = ROOT / "src/cifereca/cli.py"
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _module_source(path: Path) -> str:
    return ast.unparse(ast.parse(path.read_text()))


def _method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text())
    if path == MAIN and method_name == "refresh_library":
        tree = ast.parse(MAIN.with_name("library_refresh.py").read_text())
        return ast.unparse(next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == method_name))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_epub_cover_supports_guide_and_first_spine_fallbacks():
    tree = ast.parse(EPUB.read_text())
    constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    source = ast.unparse(tree)

    assert "guide" in constants
    assert "first_spine" in constants
    assert "pages.extend" in source
    assert "len(refs) != 1" in source


def test_cover_provenance_and_content_cache_are_persisted():
    source = _module_source(EPUB)

    assert "'source': 'epub'" in source
    assert "'member': member" in source
    assert "'cache_name': _publish_cover(data, kind)" in source
    assert "'method': method" in source
    assert "hashlib.sha256(data).hexdigest()" in source


def test_cover_metadata_schema_is_versioned_and_negative_cache_can_be_reinspected():
    epub_source = _module_source(EPUB)
    catalog_source = _method_source(CATALOG, "LocalCatalog", "_enrich_epub_entry")

    assert "SCHEMA = 2" in epub_source
    assert "cached.get('epub_metadata_schema') == EPUB_METADATA_SCHEMA and (not force)" in catalog_source
    assert "if cached.get('epub_cover') and (not cached_cover_url(cached)):" in catalog_source
    assert "inspected = inspect_epub(path)" in catalog_source
    assert "cached.pop('epub_cover', None)" in catalog_source


def test_catalog_scan_force_covers_reinspects_each_artifact_once():
    source = _method_source(CATALOG, "LocalCatalog", "scan")

    assert "self._force_covers = force_covers" in source
    assert "self._cover_refresh_seen = set()" in source


def test_cli_refresh_covers_uses_catalog_force_flag():
    source = _module_source(CLI)

    assert "scan.add_argument('--refresh-covers', action='store_true'" in source
    assert "service.catalog_scan(force_covers=a.refresh_covers)" in source


def test_library_cover_action_forces_reinspection_without_download():
    module = ast.parse(MAIN.with_name("library_page_controller.py").read_text(encoding="utf-8"))
    build = ast.unparse(next(n for n in module.body if isinstance(n, ast.FunctionDef)
                             and n.name == "build_library_page"))
    refresh = _method_source(MAIN, "MainWindow", "refresh_library")

    assert "refresh_covers=True" in build
    assert "self.service.catalog_scan(force_covers=True)" in refresh
    assert "self.service.library_entries(identity_id=identity_id, scan=scan and (not refresh_covers))" in refresh
    for forbidden in ("download", "execute_acquisition", "obtain_search_item"):
        assert forbidden not in refresh
