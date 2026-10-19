"""Summary toggle depends on rendered Pango line count, not source length (#1018)."""
from __future__ import annotations

import ast
import math
from pathlib import Path
from types import SimpleNamespace


PANEL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


class FakeClock:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 1

    def connect(self, signal, callback):
        assert signal == "after-paint"
        identifier = self.next_id
        self.next_id += 1
        self.callbacks[identifier] = callback
        return identifier

    def disconnect(self, identifier):
        self.callbacks.pop(identifier)

    def paint(self):
        for cb in tuple(self.callbacks.values()):
            cb(self)


class FakeWidget:
    def __init__(self):
        self.children = []
        self.handlers = {}
        self.visible = True
        self.focused = False

    def connect(self, name, callback):
        self.handlers[name] = callback

    def set_hexpand(self, *_):
        pass

    def append(self, child):
        self.children.append(child)

    def set_visible(self, value):
        self.visible = value

    def has_focus(self):
        return self.focused

    def grab_focus(self):
        self.focused = True

    def set_halign(self, *_):
        pass

    def add_css_class(self, *_):
        pass


class FakeLabel(FakeWidget):
    def __init__(self, label, xalign=0):
        super().__init__()
        self.text = label
        self.width = 120
        self.lines = -1
        self.ellipsize = "NONE"
        self.serial = 1
        self.clock = FakeClock()
        self.rooted = True

    def set_wrap(self, *_):
        pass

    def set_wrap_mode(self, *_):
        pass

    def set_selectable(self, *_):
        pass

    def set_lines(self, lines):
        self.lines = lines

    def get_lines(self):
        return self.lines

    def set_ellipsize(self, mode):
        self.ellipsize = mode

    def get_ellipsize(self):
        return self.ellipsize

    def get_width(self):
        return self.width

    def get_pango_context(self):
        return SimpleNamespace(get_serial=lambda: self.serial)

    def get_scale_factor(self):
        return 1

    def get_root(self):
        return object() if self.rooted else None

    def get_frame_clock(self):
        return self.clock

    def create_pango_layout(self, text):
        return FakeLayout(text)


class FakeLayout:
    def __init__(self, text):
        self.text = text
        self.width = 0

    def set_wrap(self, value):
        assert value == "WORD_CHAR"

    def set_width(self, width):
        self.width = width

    def get_line_count(self):
        chars_per_line = max(1, (self.width // 1024) // 8)
        return sum(max(1, math.ceil(len(line) / chars_per_line))
                   for line in self.text.split("\n"))


class FakeButton(FakeWidget):
    def __init__(self, label):
        super().__init__()
        self.label = label

    def set_label(self, label):
        self.label = label

    def click(self):
        self.handlers["clicked"](self)


class FakeBox(FakeWidget):
    def __init__(self, orientation, spacing):
        super().__init__()


class FakeGroup(FakeWidget):
    def set_title(self, title):
        self.title = title

    def add(self, child):
        self.children.append(child)


def _panel():
    source = PANEL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
    fn = next(x for x in owner.body if isinstance(x, ast.FunctionDef)
              and x.name == "_show_description")
    namespace = {
        "Adw": SimpleNamespace(PreferencesGroup=FakeGroup),
        "Gtk": SimpleNamespace(Label=FakeLabel, Box=FakeBox, Button=FakeButton,
                               Orientation=SimpleNamespace(VERTICAL=1),
                               Align=SimpleNamespace(START=1)),
        "Pango": SimpleNamespace(WrapMode=SimpleNamespace(WORD_CHAR="WORD_CHAR"),
                                 EllipsizeMode=SimpleNamespace(END="END", NONE="NONE"),
                                 SCALE=1024),
        "description_text": lambda value: value,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 str(PANEL), "exec"), namespace)
    groups = []
    panel = SimpleNamespace(_append_content=groups.append)
    panel._show_description = namespace["_show_description"].__get__(panel)
    return panel, groups


def _render(text, width):
    panel, groups = _panel()
    panel._show_description(text)
    assert len(groups) == 1
    box = groups[0].children[0]
    label, toggle = box.children
    label.width = width
    label.handlers["map"](label)
    return label, toggle


def test_long_summary_fitting_six_visual_lines_has_no_empty_action():
    label, toggle = _render("m" * 720, 1200)
    assert label.lines == -1
    assert label.ellipsize == "NONE"
    assert not toggle.visible


def test_hidden_lines_show_toggle_and_expand_then_collapse():
    label, toggle = _render("m" * 720, 180)
    assert label.lines == 6
    assert label.ellipsize == "END"
    assert toggle.visible and toggle.label == "Lire la suite"
    toggle.click()
    assert label.lines == -1 and label.ellipsize == "NONE"
    assert toggle.label == "Réduire"
    toggle.click()
    assert label.lines == 6 and toggle.label == "Lire la suite"


def test_reflow_font_and_unmap_keep_toggle_consistent_without_a_running_timer():
    label, toggle = _render("m" * 250, 200)
    assert toggle.visible
    label.width = 900
    label.clock.paint()
    assert not toggle.visible and label.lines == -1
    label.width = 200
    label.serial += 1
    label.clock.paint()
    assert toggle.visible
    label.handlers["unmap"](label)
    assert not label.clock.callbacks


def test_new_summary_is_independent_and_empty_summary_has_no_group():
    panel, groups = _panel()
    panel._show_description("")
    assert groups == []
    panel._show_description("Bref.")
    label, toggle = groups[0].children[0].children
    label.width = 160
    label.handlers["map"](label)
    assert not toggle.visible
