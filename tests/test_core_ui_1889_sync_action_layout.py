"""The real layout retains button identity, captions and callbacks while wrapping."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


class Widget:
    def __init__(self, **kwargs):
        self.children = []
        self.parent = None
        self.options = dict(kwargs)
    def append(self, child):
        assert child.parent is None
        child.parent = self
        self.children.append(child)
    def insert(self, child, position):
        assert position == -1
        self.append(child)
    def set_child(self, child):
        self.append(child)
    def set_policy(self, horizontal, vertical):
        self.options["policy"] = (horizontal, vertical)
    def get_first_child(self):
        return self.children[0] if self.children else None
    def get_parent(self):
        return self.parent
    def get_next_sibling(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = siblings.index(self) + 1
        return siblings[index] if index < len(siblings) else None
    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda value: self.options.__setitem__(name[4:], value)
        raise AttributeError(name)


class Label(Widget):
    pass


def methods():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_action_layout.py"
    tree = ast.parse(path.read_text())
    declarations = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    env = {"Gtk": NS(Box=Widget, FlowBox=Widget, ScrolledWindow=Widget,
                     PolicyType=NS(AUTOMATIC=1, NEVER=0),
                     FlowBoxChild=type("FlowChild", (), {}), Label=Label,
                     Orientation=NS(VERTICAL=1), SelectionMode=NS(NONE=0),
                     Align=NS(CENTER=3, START=1))}
    exec(compile(ast.Module(body=declarations, type_ignores=[]), str(path), "exec"), env)
    return env["add_sync_action"], env["sync_action_row"]


def test_buttons_are_same_objects_below_full_width_description_and_keep_callbacks():
    add, composed = methods()
    row = Widget(title="Compte au nom long", subtitle="Long état de plusieurs lignes")
    assert composed(row).children == [row]
    buttons = [Widget(sensitive=enabled, tooltip="Portée du compte") for enabled in (True, False)]
    events = []
    buttons[0].clicked = lambda: events.append("same callback")
    captions = [Label(text="Actualiser les notices du compte"), Label(text="Vérifier les fichiers…")]
    for button, caption in zip(buttons, captions):
        button.append(caption)
        add(row, button)
    wrapper = composed(row)
    assert wrapper.children[0] is row and len(wrapper.children) == 2
    viewport = wrapper.children[1]
    flow = viewport.children[0]
    assert viewport.options["min_content_width"] == 0
    assert viewport.options["propagate_natural_width"] is False
    assert flow.children == buttons
    assert flow.options["min_children_per_line"] == 1
    assert flow.options["max_children_per_line"] == 3
    assert flow.options["selection_mode"] == 0 and flow.options["homogeneous"] is False
    assert all(button.options["valign"] == 3 for button in buttons)
    assert all(button.options["halign"] == 1 for button in buttons)
    assert all(button.options["hexpand"] is False for button in buttons)
    assert all(caption.options["wrap"] is True for caption in captions)
    assert all(caption.options["width_chars"] == 28 for caption in captions)
    assert [button.options["sensitive"] for button in buttons] == [True, False]
    assert row.options["subtitle"] == "Long état de plusieurs lignes"
    buttons[0].clicked()
    assert events == ["same callback"]
