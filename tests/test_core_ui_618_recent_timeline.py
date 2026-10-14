from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from cifereca.gui.recent_time import format_recent_timestamp


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
SERVICES = ROOT / "src/cifereca/services.py"
RECENT_ROWS = ROOT / "src/cifereca/gui/recent_rows.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    if path == MAIN and method_name == "_render_recent":
        module = ast.parse(MAIN.with_name("library_render_controller.py").read_text())
        return ast.unparse(next(node for node in module.body
                               if isinstance(node, ast.FunctionDef) and node.name == "render_recent"))
    if path == SERVICES and method_name == "recent_acquisitions":
        module = ast.parse((ROOT / "src/cifereca/core/library_projection.py").read_text())
        return ast.unparse(next(n for n in module.body
                               if isinstance(n, ast.FunctionDef) and n.name == method_name))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_recent_time_labels_today_yesterday_and_older():
    now = datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc)
    assert format_recent_timestamp(int(datetime(2026, 10, 8, 8, 15, tzinfo=timezone.utc).timestamp()), now=now) == "Aujourd’hui · 08:15"
    assert format_recent_timestamp(int(datetime(2026, 10, 7, 18, 5, tzinfo=timezone.utc).timestamp()), now=now) == "Hier · 18:05"
    assert format_recent_timestamp(int(datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc).timestamp()), now=now) == "03/10/2026 · 09:00"


def test_recent_time_handles_unknown_timestamp():
    assert format_recent_timestamp(None) == "Date inconnue"
    assert format_recent_timestamp(0) == "Date inconnue"
    assert format_recent_timestamp("not-a-date") == "Date inconnue"


def test_recent_service_orders_by_materialization_created_at_not_updated_at():
    source = _method(SERVICES, "LibraryService", "recent_acquisitions")
    assert "created_at" in source
    assert "updated_at" in source
    assert "acquired_at" in source
    assert source.index("created_at") < source.index("updated_at")


def test_recent_list_exposes_compact_temporal_label():
    tree = ast.parse(RECENT_ROWS.read_text(encoding="utf-8"))
    source = ast.unparse(next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "project_recent_rows"
    ))
    assert "format_recent_timestamp(item.get('acquired_at'))" in source
    assert "temporal" in source
    facade = _method(MAIN, "MainWindow", "_render_recent")
    assert "project_recent_rows(items, row_factory=BookListItem)" in facade
    assert "window._attach_row_facets(rows, window._recent_objects, window.search_controls)" in facade


def test_recent_detail_prioritizes_filename_date_and_provenance():
    source = _method(MAIN, "MainWindow", "_recent_selected")
    assert "Path(full_path).name" in source
    assert "format_recent_timestamp(item.get('acquired_at'), detailed=True)" in source
    assert "('Fichier', file_name)" in source
    assert "('Récupéré', acquired)" in source
    assert "('Provenance', provider.capitalize())" in source
    assert "('Chemin', full_path or '—')" in source
