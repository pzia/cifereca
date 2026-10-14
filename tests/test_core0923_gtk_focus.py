from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


CONTROLLER = SOURCE.with_name("route_display_controller.py")


def _show_route():
    # Test the real owning function with an injected GTK witness, not a
    # detached AST copy of the MainWindow facade.
    from cifereca.gui.route_display_controller import show_route

    return lambda host, route: show_route(host, route, Gtk=Gtk)


class _Window:
    pass


class _DirectionType:
    TAB_FORWARD = "tab-forward"


Gtk = SimpleNamespace(Window=_Window, DirectionType=_DirectionType)


class _Widget:
    def __init__(self, root):
        self._root = root
        self.focus_calls = []

    def get_root(self):
        return self._root

    def child_focus(self, direction):
        self.focus_calls.append(direction)


def _host(widget):
    return SimpleNamespace(
        _pages={"x": SimpleNamespace(title="X")},
        view_stack=SimpleNamespace(set_visible_child_name=lambda route: None),
        header_subtitle=SimpleNamespace(set_label=lambda label: None),
        _refresh_header_actions=lambda route: None,
        _plugin_page_widgets={"x": widget},
        _render_dashboard=lambda: None,
        _render_library=lambda rows: None,
        _library_entries_cache=[],
        refresh_library=lambda *args: None,
        _render_recent=lambda: None,
        refresh_recent=lambda: None,
    )


def test_route_without_window_root_does_not_request_child_focus():
    method = _show_route()
    method.__globals__["Gtk"] = Gtk
    widget = _Widget(root=object())

    method(_host(widget), "x")

    assert widget.focus_calls == []


def test_route_rooted_in_window_requests_forward_focus():
    method = _show_route()
    method.__globals__["Gtk"] = Gtk
    widget = _Widget(root=_Window())

    method(_host(widget), "x")

    assert widget.focus_calls == [_DirectionType.TAB_FORWARD]


def test_focus_guard_is_local_and_does_not_suppress_gtk_warnings():
    source = CONTROLLER.read_text(encoding="utf-8")
    method_source = ast.unparse(next(node for node in ast.parse(source).body
                                     if isinstance(node, ast.FunctionDef) and node.name == "show_route"))

    assert "isinstance(widget.get_root(), Gtk.Window)" in method_source
    assert "child_focus(Gtk.DirectionType.TAB_FORWARD)" in method_source
    assert "warnings.filterwarnings" not in method_source
    assert "set_log_handler" not in method_source
