from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    if name == "_activate_identity":
        owner = ast.parse(MAIN.with_name("identity_activation_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef) and node.name == "activate_identity"))
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_identity_change_clears_history_and_booking_models_and_views():
    source = _method("_activate_identity")
    for fragment in (
        "window.service.last_history = []",
        "window.service.last_bookings = []",
        "window.history_objects = {}",
        "window.booking_objects = {}",
        "window._history_all_rows = []",
        "window._history_rows = []",
        "window.history_page.clear_rows()",
        "window.booking_page.clear_rows()",
    ):
        assert fragment in source


def test_identity_context_generation_invalidates_callbacks():
    source = _method("_activate_identity")
    assert "window._identity_context_generation += 1" in source
    assert "identity_generation = window._identity_context_generation" in source
    assert "window._identity_request_current(identity.identity_id, identity_generation)" in source


def test_history_refresh_only_applies_to_current_identity_context():
    source = _method("refresh_history")
    assert "identity_id = current.identity_id if current is not None else None" in source
    assert "generation = self._identity_context_generation" in source
    assert "self._identity_request_current(identity_id, generation)" in source
    assert "self._populate_history(rows)" in source


def test_booking_refresh_only_applies_to_current_identity_context():
    source = _method("refresh_bookings")
    assert "identity_id = current.identity_id if current is not None else None" in source
    assert "generation = self._identity_context_generation" in source
    assert "self._identity_request_current(identity_id, generation)" in source
    assert "self._populate_bookings(rows)" in source


def test_active_secondary_page_reloads_after_identity_loans_finish():
    source = _method("_activate_identity")
    assert "route == 'history'" in source
    assert "window.refresh_history(False)" in source
    assert "route == 'bookings'" in source
    assert "window.refresh_bookings(False)" in source


def test_identity_request_guard_requires_both_identity_and_generation():
    source = _method("_identity_request_current")
    assert "generation == self._identity_context_generation" in source
    assert "identity_id == current_identity_id" in source
