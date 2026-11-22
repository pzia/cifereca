"""Toolbar styling and insets must not push overflow outside the viewport."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.action_layout import action_budget, compact_actions, visible_action_count


class Button:
    def __init__(self, width):
        self.width = width
        self.parent = None
        self.visible = False

    def get_parent(self): return self.parent
    def set_valign(self, value): self.valign = value
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
               and n.name in {'_layout_overflow', '_move_button', 'append', '_resize'}]
    owner = ast.ClassDef(name='Bar', bases=[], keywords=[], body=methods, decorator_list=[])
    class Content:
        def set_label(self, value): self.label = value
        def set_icon_name(self, value): self.icon_name = value

    ns = {
        'Gtk': NS(
            Orientation=NS(HORIZONTAL=0),
            Align=NS(CENTER=0),
            Button=Button,
            AccessibleProperty=NS(LABEL=1),
            Image=NS(new_from_icon_name=lambda name: NS(label='', icon_name=name)),
        ),
        'Adw': NS(ButtonContent=Content),
        'visible_action_count': visible_action_count,
        'action_budget': action_budget, 'compact_actions': compact_actions,
    }
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


def test_resize_keeps_the_overflow_menu_attached_while_actions_are_hidden(monkeypatch):
    obj = bar()
    obj._layout_overflow(410)
    menu = obj.overflow_button
    removals = []
    remove = obj.box.remove

    def observe_remove(widget):
        if widget is menu:
            removals.append(widget)
        remove(widget)

    monkeypatch.setattr(obj.box, "remove", observe_remove)

    for budget in (650, 300):
        previous = obj._overflow_visible_count
        obj._layout_overflow(budget)
        assert obj._overflow_visible_count != previous
        assert obj.overflow_box.children
        assert menu.get_visible() and menu.get_parent() is obj.box
        assert obj.box.children[-1] is menu
    assert not removals

    obj._layout_overflow(1400)
    assert removals == [menu]
    assert not menu.get_visible() and menu.get_parent() is None


def test_late_toolbar_style_change_reduces_visible_count():
    obj = bar()
    obj._layout_overflow(410)
    for button in obj.box.children:
        if button is not obj.overflow_button:
            button.width = 175
    obj._layout_overflow(410)
    assert occupied(obj) <= 410
    assert obj._overflow_visible_count == 1


class LongPrimary(Button):
    """Natural GTK-ish text width, no display required."""
    def __init__(self, label):
        super().__init__(len(label) * 8 + 36)
        self._cifereca_action_label = label
        self._cifereca_action_icon = 'document-open-symbolic'
        self.display = label
        self.accessible = None

    def set_child(self, content):
        self.display = content.label
        self.width = max(32, len(self.display) * 8 + 36)

    def set_label(self, value):
        self.display = value
        self.width = max(32, len(value) * 8 + 36)

    def update_property(self, _keys, values):
        self.accessible = values[0]


def test_p1_3687_oversized_primary_label_keeps_overflow_hittable_at_1024():
    obj = bar()
    full_label = (
        "Consulter disponibilités et actions de la notice 1 ici · Premier"
    )
    original = obj._buttons[0]
    obj.box.remove(original)
    primary = LongPrimary(full_label)
    obj._buttons[0] = primary
    obj.box.append(primary)
    obj._widths.pop(original)
    obj._widths[primary] = primary.width
    obj.preserve_primary_label = True
    obj._primary_full_width = None
    obj._primary_fit_budget = None
    obj._primary_variant = None
    obj._compact = False

    obj._layout_overflow(529)
    assert obj._primary_variant == 'abbreviated'
    assert primary.display.endswith('…')
    assert primary.accessible == full_label
    assert obj.box.children[0] is primary
    assert obj.box.children[-1] is obj.overflow_button
    assert occupied(obj) <= 529
    assert obj.overflow_button.visible
    # When the panel grows, the original meaningful title must return.
    obj._layout_overflow(950)
    assert obj._primary_variant is None
    assert primary.display == full_label
    assert primary.accessible == full_label
    assert occupied(obj) <= 950
    assert obj._buttons[0] is primary
    # Narrow again: no stale dimensions or duplicate button identities.
    obj._layout_overflow(529)
    assert obj._primary_variant == 'abbreviated'
    assert occupied(obj) <= 529
    assert obj.box.children[-1] is obj.overflow_button


def test_p1_3687_full_caption_remains_if_all_actions_fit_without_menu():
    obj = bar()
    for button in tuple(obj.box.children):
        obj.box.remove(button)
    full_label = "Consulter la notice avec sa provenance attestée"
    primary = LongPrimary(full_label)
    secondary = Button(32)
    obj._buttons = [primary, secondary]
    obj._widths = {primary: primary.width, secondary: 32}
    obj.box.append(primary)
    obj.box.append(secondary)
    obj.preserve_primary_label = True
    obj._primary_full_width = None
    obj._primary_fit_budget = None
    obj._primary_variant = None
    obj._compact = False
    # The two actual buttons fit; reserving an unnecessary menu would
    # otherwise shorten the primary caption for no UX benefit.
    budget = primary.width + 32 + obj.box.spacing + 2 * obj.box.margins
    obj._layout_overflow(budget)
    assert primary.display == full_label
    assert obj._primary_variant is None
    assert not obj.overflow_button.visible
    assert obj.box.children == [primary, secondary]
    assert occupied(obj) <= budget


def test_late_contextual_action_preserves_priority_when_toolbar_grows():
    obj = bar()
    obj._layout_overflow(410)
    original = list(obj._buttons)
    late = Button(120)
    late._cifereca_action_label = "Action tardive"
    obj.append(late)
    # The newly evaluated action is initially on the toolbar. Older
    # secondary actions must return from the popover *before* it.
    obj._layout_overflow(1400)
    assert obj.box.children == original + [late]
    assert obj.overflow_box.children == []
    assert not obj.overflow_button.visible
    assert occupied(obj) <= 1400


def test_late_actions_keep_exact_identity_and_priority_on_partial_growth():
    obj = bar()
    obj._layout_overflow(410)
    original = list(obj._buttons)
    late = [Button(120), Button(120)]
    for button in late:
        button._cifereca_action_label = "Action tardive"
        obj.append(button)
    for budget in (650, 410, 1500, 300, 650):
        obj._layout_overflow(budget)
        count = obj._overflow_visible_count
        assert obj.box.children == (original + late)[:count] + (
            [obj.overflow_button] if count < len(original + late) else []
        )
        assert obj.overflow_box.children == (original + late)[count:]
        assert occupied(obj) <= budget


def test_empty_action_bar_releases_width_and_late_actions_recover_budget():
    obj = bar()
    obj._buttons = []
    obj._widths = {}
    obj._budget = 640
    obj.source = NS(get_width=lambda: 1189)
    obj.fraction = 0.5
    obj.overflow = False
    obj._compact = False
    requests = []
    obj.set_size_request = lambda width, height: requests.append((width, height))

    # An empty bar cannot force its parent to grow to accommodate a
    # phantom half-width alongside the page heading.
    assert obj._resize(None, None)
    assert obj._budget == 0 and requests == [(0, -1)]
    assert obj._resize(None, None)
    assert requests == [(0, -1)]

    # Actual actions retain their existing responsive size and identity.
    button = Button(120)
    obj._buttons = [button]
    obj._widths = {button: 120}
    obj.source = NS(get_width=lambda: 700)
    assert obj._resize(None, None)
    assert obj._budget == 350 and requests[-1] == (350, -1)
    assert obj._buttons == [button]
    obj._buttons = []
    assert obj._resize(None, None)
    assert requests[-1] == (0, -1)
