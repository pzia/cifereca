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
