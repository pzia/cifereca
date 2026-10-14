"""P0 #1132 — activity history and pending error use one visible footer."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.activity_display_controller import (
    refresh_activity_history, show_pending_activity_error,
)


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("activity_display_controller.py")


class Widget:
    def __init__(self, *, label=None, xalign=None):
        self.calls = []
        self.label = label
        self.children = []

    def __getattr__(self, name):
        if name.startswith(("set_", "add_", "remove_")) or name == "stop":
            return lambda *args: self.calls.append((name, *args))
        raise AttributeError(name)

    def append(self, child):
        self.children.append(child)


def _host(*, entries=(), pending=None):
    events = []
    feedback = NS(
        rows=lambda: tuple(entries),
        latest_unacknowledged_error=lambda: pending,
    )
    widgets = {name: Widget() for name in (
        "busy_revealer", "busy_spinner", "activity_status_icon",
        "busy_label", "activity_dismiss",
        "activity_history_box", "activity_history_button",
    )}
    return NS(_closed=False, activity_feedback=feedback, **widgets), events


def test_headless_owner_and_preserved_two_public_facades():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and ("import gi" in ast.unparse(node) or "main_window" in ast.unparse(node))
        for node in ast.walk(owner)
    )
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    funcs = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "activity_display_controller.refresh_activity_history" in ast.unparse(
        funcs["_refresh_activity_history"]
    )
    assert "activity_display_controller.show_pending_activity_error" in ast.unparse(
        funcs["_show_pending_activity_error"]
    )


def test_history_projection_preserves_order_warning_style_and_one_row_per_entry():
    host, events = _host(entries=(NS(display_text="a"), NS(display_text="b")))
    labels = []
    def label(**kwargs):
        item = Widget(**kwargs)
        labels.append(item)
        return item
    def clear(container):
        events.append("clear")
        container.children.clear()
    def project(rows):
        events.append(("project", len(rows)))
        return (("Terminé · premier", False), ("Erreur · second", True))

    host.activity_history_box.children.append("obsolete")
    refresh_activity_history(
        host, Gtk=NS(Label=label), clear_box=clear,
        activity_history_rows=project,
    )
    assert events == ["clear", ("project", 2)]
    assert host.activity_history_box.children == labels
    assert [x.label for x in labels] == ["Terminé · premier", "Erreur · second"]
    assert ("add_css_class", "caption") in labels[0].calls
    assert ("add_css_class", "warning") not in labels[0].calls
    assert ("add_css_class", "warning") in labels[1].calls
    assert ("set_visible", True) in host.activity_history_button.calls


def test_empty_history_hides_history_button_without_new_widgets():
    host, events = _host()
    refresh_activity_history(
        host, Gtk=NS(Label=lambda **kwargs: (_ for _ in ()).throw(AssertionError())),
        clear_box=lambda box: box.children.clear(),
        activity_history_rows=lambda rows: (),
    )
    assert ("set_visible", False) in host.activity_history_button.calls
    assert host.activity_history_box.children == []


def test_closed_or_unbuilt_history_does_not_access_widgets_or_project_rows():
    closed = NS(_closed=True)
    assert refresh_activity_history(
        closed, Gtk=None, clear_box=None, activity_history_rows=None,
    ) is None
    host = NS(_closed=False)
    assert refresh_activity_history(
        host, Gtk=None, clear_box=None, activity_history_rows=None,
    ) is None


def test_pending_error_occupies_banner_and_never_schedules_auto_hide():
    host, _ = _host(pending=NS(display_text="Erreur · connexion impossible"))
    assert show_pending_activity_error(host) is True
    assert ("stop",) in host.busy_spinner.calls
    assert ("set_from_icon_name", "dialog-warning-symbolic") in host.activity_status_icon.calls
    assert ("add_css_class", "activity-error") in host.activity_status_icon.calls
    assert ("remove_css_class", "activity-success") in host.activity_status_icon.calls
    assert ("set_label", "Erreur · connexion impossible") in host.busy_label.calls
    assert ("set_visible", True) in host.activity_dismiss.calls
    assert ("set_reveal_child", True) in host.busy_revealer.calls


def test_missing_error_or_banner_leaves_activity_view_untouched():
    host, _ = _host()
    assert show_pending_activity_error(host) is False
    assert host.busy_spinner.calls == []
    del host.busy_revealer
    host.activity_feedback.latest_unacknowledged_error = lambda: NS(
        display_text="Exception"
    )
    assert show_pending_activity_error(host) is False
    assert host.activity_status_icon.calls == []


def test_closed_window_cannot_resurrect_pending_error():
    assert show_pending_activity_error(NS(_closed=True)) is False
