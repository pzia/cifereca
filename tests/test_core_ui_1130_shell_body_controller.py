"""#1130: shell body GTK assembly, activity bar and lifecycle invariants."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.navigation_history import NavigationHistory
from cifereca.gui.shell_body_controller import build_main_shell


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "shell_body_controller.py"
MAIN = ROOT / "main_window.py"


class Widget:
    def __init__(self, **options):
        self.options = options
        self.children = []
        self.properties = {}
        self.callbacks = {}

    def append(self, child):
        self.children.append(child)

    def set_child(self, child):
        self.child = child

    def set_popover(self, value):
        self.popover = value

    def connect(self, signal, callback):
        self.callbacks[signal] = callback

    def click(self):
        self.callbacks["clicked"](self)

    def __getattr__(self, attr):
        if attr.startswith("set_"):
            return lambda *args: self.properties.__setitem__(attr[4:], args[0] if len(args) == 1 else args)
        raise AttributeError(attr)


class Image(Widget):
    @classmethod
    def new_from_icon_name(cls, name):
        return cls(icon_name=name)


class Button(Widget):
    @classmethod
    def new_from_icon_name(cls, name):
        return cls(icon_name=name)


GTK = NS(
    Box=Widget, Label=Widget, ListBox=Widget, Stack=Widget, Revealer=Widget,
    Spinner=Widget, Image=Image, MenuButton=Widget, Popover=Widget, Button=Button,
    ListBoxRow=Widget,
    Orientation=NS(VERTICAL="vertical", HORIZONTAL="horizontal"),
    SelectionMode=NS(SINGLE="single"),
    StackTransitionType=NS(CROSSFADE="crossfade", NONE="none"),
    RevealerTransitionType=NS(SLIDE_UP="slide-up"),
)


def _window():
    events, styles = [], []
    header = Widget(label="header")
    window = NS(
        _closed=False,
        _create_shell_header=lambda: events.append("header") or header,
        _nav_selected=lambda *args: events.append(("nav-selected", args)),
        _rebuild_plugin_pages=lambda: events.append("pages"),
        navigate=lambda route: events.append(("navigate", route)),
        _dismiss_activity_feedback=lambda: events.append("dismiss"),
    )
    def css(widget, *classes):
        styles.append((widget, classes))
    return window, events, styles, css, header


def _render():
    window, events, styles, css, header = _window()
    root = build_main_shell(window, Gtk=GTK, add_css=css, version="1.2.3")
    return window, root, events, styles, header


def test_owner_has_no_runtime_gtk_import_and_mainwindow_facade_is_stable():
    src = OWNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert not any("import gi" in ast.unparse(node) or "main_window" in ast.unparse(node)
                   for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(x for x in main.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    facade = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                  and x.name == "_build_main_shell")
    assert [arg.arg for arg in facade.args.args] == ["self"]
    text = ast.unparse(facade)
    assert "shell_body_controller.build_main_shell" in text
    assert "Gtk=Gtk" in text and "add_css=add_css" in text and "version=__version__" in text


def test_root_contains_header_sidebar_stack_and_exactly_one_footer():
    window, root, events, styles, header = _render()
    assert window.shell_root is root
    assert root.children[0] is header
    assert len(root.children) == 3
    body, footer = root.children[1:]
    assert body.children == [window.sidebar, window.view_stack]
    assert footer is window.busy_revealer
    assert footer.child.children == [
        window.busy_spinner, window.activity_status_icon, window.busy_label,
        window.activity_history_button, window.activity_dismiss,
    ]
    assert sum(c is window.busy_revealer for c in root.children) == 1


def test_navigation_history_is_per_window_and_initialized_before_page_rebuild():
    a, _, events_a, _, _ = _render()
    b, _, events_b, _, _ = _render()
    assert isinstance(a._navigation_history, NavigationHistory)
    assert isinstance(b._navigation_history, NavigationHistory)
    assert a._navigation_history is not b._navigation_history
    assert a._restoring_navigation is False
    assert events_a == ["header", "pages", ("navigate", "dashboard")]
    assert events_b == events_a


def test_sidebar_visible_width_and_brand_version_keep_css():
    window, root, events, styles, header = _render()
    assert window.sidebar.properties["size_request"] == (160, -1)
    brand, nav = window.sidebar.children
    assert brand.options["label"] == "v1.2.3"
    assert brand.properties["margin_top"] == 14
    assert brand.properties["margin_start"] == 16
    assert brand.properties["margin_bottom"] == 4
    assert nav is window.nav_list
    assert (window.sidebar, ("sidebar",)) in styles
    assert (brand, ("caption", "dim-label")) in styles


def test_main_stack_is_non_homogeneous_to_prevent_hidden_page_width_lock():
    window, *_ = _render()
    # #2027 isolation experiment: only the main shell transition changes.
    assert window.view_stack.properties["transition_type"] == "none"
    assert window.view_stack.properties["hhomogeneous"] is False
    assert window.view_stack.properties["hexpand"] is True
    assert window.view_stack.properties["vexpand"] is True
    assert window.shell_root.children[1].properties["vexpand"] is True


def test_navigation_list_connects_only_one_owner_callback_with_single_click():
    window, root, events, styles, header = _render()
    assert window.nav_list.properties["selection_mode"] == "single"
    assert window.nav_list.properties["activate_on_single_click"] is True
    assert len(window.nav_list.callbacks) == 1
    assert "row-selected" in window.nav_list.callbacks
    window.nav_list.callbacks["row-selected"]("row")
    assert events[-1] == ("nav-selected", ("row",))
    assert window.nav_rows == {}
    assert window._nav_groups == {}
    assert window._nav_group_expanded == {}


def test_activity_widgets_initially_hidden_with_stable_labels_and_css():
    window, root, events, styles, header = _render()
    assert window.busy_revealer.properties["transition_type"] == "slide-up"
    footer_content = window.busy_revealer.child
    assert (footer_content, ("busy-bar",)) in styles
    assert window.busy_spinner.properties["visible"] is False
    assert window.activity_status_icon.options["icon_name"] == "emblem-ok-symbolic"
    assert window.activity_status_icon.properties["visible"] is False
    assert window.busy_label.options["label"] == ""
    assert window.busy_label.properties["hexpand"] is True
    assert window.busy_label.properties["wrap"] is True


def test_history_popover_has_one_box_and_remains_hidden_until_activity():
    window, root, events, styles, header = _render()
    menu = window.activity_history_button
    assert menu.options["label"] == "Historique"
    assert menu.properties["icon_name"] == "view-list-symbolic"
    assert menu.properties["visible"] is False
    assert menu.properties["tooltip_text"] == "Historique des opérations"
    assert menu.popover.child is window.activity_history_box
    assert window.activity_history_box.properties["margin_top"] == 8
    assert window.activity_history_box.properties["margin_start"] == 10


def test_footer_dismiss_callback_uses_current_window_only_on_click():
    window, root, events, styles, header = _render()
    assert window.activity_dismiss.options["label"] == "Acquitter"
    assert window.activity_dismiss.properties["visible"] is False
    assert window.activity_dismiss.properties["tooltip_text"] == "Acquitter les erreurs affichées (historique conservé)"
    assert events == ["header", "pages", ("navigate", "dashboard")]
    window.activity_dismiss.click()
    assert events[-1] == "dismiss"
    assert len(window.activity_dismiss.callbacks) == 1


def test_initial_dashboard_navigation_never_precedes_plugin_page_construction():
    window, root, events, styles, header = _render()
    assert events.index("pages") < events.index(("navigate", "dashboard"))
    assert events.count(("navigate", "dashboard")) == 1


def test_activity_bar_is_not_a_second_notification_row():
    window, root, events, styles, header = _render()
    assert root.children[-1] is window.busy_revealer
    assert window.busy_revealer.child.options["spacing"] == 9
    assert len(window.busy_revealer.child.children) == 5
    assert not hasattr(window, "notification_revealer")


def test_shell_builder_does_not_activate_plugins_or_poll_service():
    window, events, styles, css, header = _window()
    assert not hasattr(window, "service")
    assert not hasattr(window, "plugin_manager")
    build_main_shell(window, Gtk=GTK, add_css=css, version="dev")
    assert events == ["header", "pages", ("navigate", "dashboard")]


def test_2027_shell_stack_no_crossfade_experiment_preserves_busy_revealer():
    window, _, _, _, _ = _render()
    assert window.view_stack.properties["transition_type"] == "none"
    assert window.busy_revealer.properties["transition_type"] == "slide-up"
