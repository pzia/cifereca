from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_identity_change_clears_history_and_booking_models_and_views():
    source = _method("_activate_identity")
    for fragment in (
        "self.service.last_history = []",
        "self.service.last_bookings = []",
        "self.history_objects = {}",
        "self.booking_objects = {}",
        "self._history_all_rows = []",
        "self._history_rows = []",
        "self.history_page.clear_rows()",
        "self.booking_page.clear_rows()",
    ):
        assert fragment in source


def test_identity_context_generation_invalidates_callbacks():
    source = _method("_activate_identity")
    assert "self._identity_context_generation += 1" in source
    assert "identity_generation = self._identity_context_generation" in source
    assert "self._identity_request_current(identity.identity_id, identity_generation)" in source


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
    assert "self.refresh_history(False)" in source
    assert "route == 'bookings'" in source
    assert "self.refresh_bookings(False)" in source


def test_identity_request_guard_requires_both_identity_and_generation():
    source = _method("_identity_request_current")
    assert "generation == self._identity_context_generation" in source
    assert "identity_id == current_identity_id" in source
