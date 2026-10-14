from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src/cifereca/services.py"
MAIN = ROOT / "src/cifereca/gui/main_window.py"
LOANS = ROOT / "src/cifereca/gui/loans_controller.py"
MODELS = ROOT / "src/cifereca/plugin_api/models.py"


def _method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text())
    scope = (tree.body if class_name is None else next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name).body)
    method = next(node for node in scope if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def _module_source(path: Path) -> str:
    return ast.unparse(ast.parse(path.read_text()))


def _nested_class_source(path: Path, outer_name: str, inner_name: str) -> str:
    if path.name == "main_window.py" and inner_name == "_UiHost":
        from tests.gui_source import gui_source_text
        owner = ast.parse(gui_source_text(path))
        return ast.unparse(next(node for node in owner.body
                               if isinstance(node, ast.ClassDef) and node.name == inner_name))
    tree = ast.parse(path.read_text())
    outer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == outer_name)
    inner = next(node for node in outer.body if isinstance(node, ast.ClassDef) and node.name == inner_name)
    return ast.unparse(inner)


def test_refresh_identity_loans_is_scoped_to_loan_capable_sessions_and_read_only_adept_refresh():
    source = _method_source(SERVICES, "LibraryService", "refresh_identity_loans")

    assert "accounts = self.accounts_for_identity(identity_id)" in source
    assert "if not session.capabilities.loans:" in source
    assert "self._loans_for_account(account, force=force)" in source
    assert "self.refresh_adept_inventory(identity_id)" in source

    for forbidden in ("opening_sync", "sync_kobo", "execute_acquisition", "download", "catalog_scan"):
        assert forbidden not in source


def test_adept_refresh_is_inventory_only():
    source = _method_source(SERVICES, "LibraryService", "refresh_adept_inventory")

    assert "self._reconcile_identity_adept_inventory(identity_id)" in source
    for forbidden in ("execute_drm_mutation", "return_adept_acquisition", "execute_acquisition", "download"):
        assert forbidden not in source


def test_late_loan_refresh_for_old_identity_is_ignored():
    source = _method_source(LOANS, None, "refresh_loans")

    assert "identity_id = identity.identity_id if identity else None" in source
    assert "if (current.identity_id if current else None) == identity_id:" in source
    assert "self._loans_refreshed(rows)" in source


def test_identity_activation_ignores_late_results_from_previous_identity():
    source = _method_source(
        MAIN.with_name("identity_activation_controller.py"),
        None, "activate_identity",
    )
    assert "identity_activation_controller.activate_identity" in _method_source(
        MAIN, "MainWindow", "_activate_identity",
    )
    assert "if not window.service.current_identity or window.service.current_identity.identity_id != identity.identity_id:" in source
    assert "return" in source


def test_filtered_identity_navigation_validates_route_identity_and_providers():
    facade = _method_source(MAIN, "MainWindow", "open_identity_view")
    source = _method_source(
        MAIN.with_name("identity_view_controller.py"), None, "open_identity_view",
    )

    assert "identity_view_controller.open_identity_view" in facade
    assert "if route not in {'search', 'library'}:" in source
    assert "snapshot = tuple(providers)" in source
    assert "selected = tuple(dict.fromkeys(snapshot))" in source
    assert "allowed = {descriptor.id for descriptor in window.service.provider_descriptors()} | {'local'}" in source
    assert "if not set(selected) <= allowed:" in source
    assert "window._activate_identity(identity_id)" in source
    assert "controls.set_state(QueryState(scope='digital', providers=selected))" in source


def test_identity_view_host_is_an_optional_extension_of_existing_identity_host():
    source = _module_source(MODELS)

    assert "class UiIdentityHost(UiPageHost, Protocol):" in source
    assert "class UiIdentityViewHost(UiIdentityHost, Protocol):" in source
    assert "def supports_identity_view_filters(self) -> bool:" in source
    assert "def open_identity_view(self, identity_id: str" in source


def test_ui_host_exposes_filtered_navigation_without_removing_old_library_broker():
    source = _nested_class_source(MAIN, "MainWindow", "_UiHost")

    assert "def open_identity_library" in source
    assert "def supports_identity_view_filters" in source
    assert "return True" in source
    assert "def open_identity_view" in source
