"""Toolbar styling and insets must not push overflow outside the viewport."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.action_layout import visible_action_count


class Button:
    def __init__(self, width):
        self.width = width
        self.parent = None
        self.visible = False

    def get_parent(self): return self.parent
    def get_visible(self): return self.visible
    def set_visible(self, value): self.visible = value
    def measure(self, *_): return (self.width, self.width, -1, -1)


class Box:
    def __init__(self, margins=12, spacing=6):
        self.children = []
        self.margins, self.spacing = margins, spacing

    def append(self, button):
        assert button.parent is None
        self.children.append(button)
        button.parent = self

    def remove(self, button):
        self.children.remove(button)
        button.parent = None

    def get_margin_start(self): return self.margins
    def get_margin_end(self): return self.margins
    def get_spacing(self): return self.spacing

    def reorder_child_after(self, button, previous):
        self.children.remove(button)
        self.children.insert(self.children.index(previous) + 1 if previous else 0, button)


def bar():
    path = Path(__file__).resolve().parents[1] / 'src/cifereca/gui/action_widgets.py'
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AdaptiveActionBar')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {'_layout_overflow', '_move_button'}]
    owner = ast.ClassDef(name='Bar', bases=[], keywords=[], body=methods, decorator_list=[])
    ns = {'Gtk': NS(Orientation=NS(HORIZONTAL=0)), 'visible_action_count': visible_action_count}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[])), str(path), 'exec'), ns)
    obj = ns['Bar']()
    obj.box, obj.overflow_box = Box(), Box(0)
    obj._buttons = [Button(120) for _ in range(8)]
    # Historical measurement before the toolbar CSS was resolved.
    obj._widths = {button: 90 for button in obj._buttons}
    obj._overflow_visible_count = obj._overflow_width = None
    obj.overflow_button = Button(54)
    for button in obj._buttons:
        obj.box.append(button)
    return obj


def occupied(obj):
    return (sum(button.width for button in obj.box.children)
            + max(0, len(obj.box.children) - 1) * obj.box.spacing + 2 * obj.box.margins)


def test_css_widths_margins_and_real_overflow_reservation_are_respected():
    obj = bar()
    obj._layout_overflow(410)
    assert occupied(obj) <= 410
    assert obj.box.children == obj._buttons[:2] + [obj.overflow_button]
    assert obj.overflow_box.children == obj._buttons[2:]
    assert obj.overflow_button.visible


def test_resize_keeps_original_buttons_and_menu_last_without_reordering_actions():
    obj = bar()
    for budget in (410, 650, 300, 1100, 410):
        obj._layout_overflow(budget)
        assert occupied(obj) <= budget
        count = obj._overflow_visible_count
        assert obj.box.children == obj._buttons[:count] + ([obj.overflow_button] if count < 8 else [])
        assert obj.overflow_box.children == obj._buttons[count:]
    previous = list(obj.box.children)
    obj._layout_overflow(410)
    assert obj.box.children == previous


def test_late_toolbar_style_change_reduces_visible_count():
    obj = bar()
    obj._layout_overflow(410)
    for button in obj.box.children:
        if button is not obj.overflow_button:
            button.width = 175
    obj._layout_overflow(410)
    assert occupied(obj) <= 410
    assert obj._overflow_visible_count == 1
