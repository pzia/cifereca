"""P0 #1127: headless tests of actual Settings shell with GTK doubles."""
from __future__ import annotations
from tests.settings_source import settings_source_text
import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.settings_shell import build_settings_shell


class Widget:
    def __init__(self, *, orientation=None, spacing=0, label=None, xalign=None):
        self.orientation, self.spacing, self.label = orientation, spacing, label
        self.children, self.classes, self.pages = [], [], []
        self.title = None
        self.policy, self.max_size = None, None
        self.bound = None
        self.text_wrap = None
        self.expand = None
        self.margins = {}
        self.props = {}
    def set_policy(self, horizontal, vertical): self.policy = (horizontal, vertical)
    def set_child(self, child): self.children = [child]
    def set_maximum_size(self, size): self.max_size = size
    def set_margin_top(self, value): self.margins["top"] = value
    def set_margin_bottom(self, value): self.margins["bottom"] = value
    def set_margin_start(self, value): self.margins["start"] = value
    def set_margin_end(self, value): self.margins["end"] = value
    def set_hexpand(self, value): self.expand = value
    def set_wrap(self, value): self.text_wrap = value
    def add_css_class(self, klass): self.classes.append(klass)
    def set_stack(self, stack): self.bound = stack
    def append(self, child): self.children.append(child)
    def add_titled(self, child, key, label): self.pages.append((child,key,label))


def _shell():
    classes = []
    gtk = SimpleNamespace(
        ScrolledWindow=Widget, Box=Widget, Label=Widget,
        Stack=Widget, StackSwitcher=Widget,
        PolicyType=SimpleNamespace(NEVER="never", AUTOMATIC="auto"),
        Orientation=SimpleNamespace(VERTICAL="vertical", HORIZONTAL="horizontal"),
    )
    shell = build_settings_shell(
        Gtk=gtk, Adw=SimpleNamespace(Clamp=Widget),
        add_css=lambda widget, *tags: classes.append((widget, tags)),
    )
    return shell, classes


def test_six_named_tabs_keep_original_order_and_labels():
    shell, classes = _shell()
    assert [(key, title) for _, key, title in shell.tabs.pages] == [
        ("accounts", "Identités et comptes"), ("library", "Bibliothèque"),
        ("sync", "Synchronisation"), ("interface", "Interface"),
        ("ai", "Intelligence artificielle"), ("diagnostic", "Diagnostic"),
    ]
    assert list(shell.sections) == [x[1] for x in shell.tabs.pages]
    assert all(widget is shell.sections[key] for widget,key,_ in shell.tabs.pages)
    assert len({id(x) for x in shell.sections.values()}) == 6


def test_scroll_clamp_heading_and_tab_switcher_match_baseline():
    shell, css = _shell()
    assert shell.scroll.policy == ("never", "auto")
    assert shell.scroll.children[0].max_size == 980
    assert shell.scroll.children[0].children == [shell.root]
    assert shell.root.margins == {"top": 22, "bottom": 28, "start": 18, "end": 18}
    assert shell.root.spacing == 18
    heading, subtitle, tab_scroller, tabs = shell.root.children
    assert shell.heading is heading
    assert heading.children[0].label == "Configuration et diagnostic"
    assert subtitle.label.startswith("Identités, sources")
    assert tab_scroller.policy == ("auto", "never")
    assert tab_scroller.children[0].bound is shell.tabs
    assert "settings-tabs" in tab_scroller.children[0].classes
    assert tabs is shell.tabs
    assert css[0][1] == ("page-title",) and css[1][1] == ("dim-label",)


def test_render_has_no_handlers_and_no_backend_dependencies():
    shell, _ = _shell()
    assert all(not hasattr(x, "signals") for x in shell.sections.values())
    assert shell.root is not None


def test_main_window_preserves_standalone_and_revision_host_ownership():
    main = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    source = ast.unparse(ast.parse(settings_source_text()))
    assert "build_settings_shell(" in source
    assert "settings_heading = shell.heading" in source
    assert "if not standalone:" in source
    assert "self._settings_tabs = tabs" in source
    assert "SettingsRevisionState(sections)" in source
    assert "settings_committer.submit(" in source
    assert 'tabs.connect("notify::visible-child-name", update_settings_actions)' in settings_source_text()
