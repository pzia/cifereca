"""Contract tests for the youth-to-known-catalogue MVP (#906), without GTK."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
YOUTH = ROOT / "src/plugins/youth-catalog/youth_page.py"
CORE = ROOT / "src/cifereca/gui/main_window.py"


def _class_method(path: Path, cls: str, method: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.ClassDef) and node.name == cls)
    return next(node for node in parent.body
                if isinstance(node, ast.FunctionDef) and node.name == method)


def test_public_master_detail_renders_youth_actions_as_standard_buttons():
    renderer = ast.unparse(_class_method(CORE, "PublicBookMasterDetailView", "show_detail"))
    assert "getattr(detail, 'actions'" in renderer
    assert "action_button(str(action.title)" in renderer
    assert "button.connect('clicked'" in renderer
    assert "panel.add_actions(buttons)" in renderer


def test_youth_906_uses_optional_host_and_retains_title_and_identity():
    selected = ast.unparse(_class_method(YOUTH, "YouthView", "selected"))
    search = ast.unparse(_class_method(YOUTH, "YouthView", "_search_library"))
    assert "getattr(self.host, 'search_books', None)" in selected
    assert "UiBookDetailAction(" in selected
    assert "id='search-library'" in selected
    assert "icon_name='system-search-symbolic'" in selected
    assert "online=False" in search
    assert "identity_id=identity_id" in search
    assert "self.host.toast(" in search


def test_host_and_core_search_route_do_not_issue_implicit_online():
    host = ast.unparse(_class_method(CORE, "_UiHost", "search_books"))
    core = ast.unparse(_class_method(CORE, "MainWindow", "search_books"))
    assert "self.window.search_books(" in host
    assert "identity_id != current_id" in core
    assert "notify=False" in core
    assert "QueryState(text=query.strip(), field=field, scope='all')" in core
    assert "self.run_search(online)" in core
    assert "run_search(True)" not in core
