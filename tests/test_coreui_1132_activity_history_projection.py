"""Headless activity history projection contracts (#1132)."""

from types import SimpleNamespace

from cifereca.gui.activity_history_projection import activity_history_rows


def test_empty_history_renders_no_rows():
    assert activity_history_rows(()) == ()


def test_order_display_and_warning_flags_preserved():
    entries = [SimpleNamespace(display_text="En cours", state="running"),
               SimpleNamespace(display_text="Échec", state="error"),
               SimpleNamespace(display_text="Terminé", state="success")]
    assert activity_history_rows(entries) == (
        ("En cours", False), ("Échec", True), ("Terminé", False),
    )


def test_projection_consumes_lazy_history_once():
    consumed = []
    def rows():
        consumed.append(1)
        yield SimpleNamespace(display_text="Résultat", state="error")
    assert activity_history_rows(rows()) == (("Résultat", True),)
    assert consumed == [1]

def test_main_window_history_uses_shared_projection_without_duplicate_rules():
    """GTK remains responsible only for rendering the projected rows."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_refresh_activity_history")
    body = ast.unparse(method)
    assert "activity_history_rows(self.activity_feedback.rows())" in body
    assert "for display_text, is_error in rows:" in body
    assert "label.add_css_class('warning')" in body
    assert "self.activity_history_button.set_visible(bool(rows))" in body
