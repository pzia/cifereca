"""#2141: real layout composition preserves primary actions and account scope."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

from tests.test_core_ui_1889_sync_action_layout import Widget
from tests import test_core_ui_1347_sync_action_scope as callbacks


class Node(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cifereca_sync_tools_state = {}
        self._cifereca_sync_source_id = "source"
        self.handlers = {}

    def connect(self, name, callback):
        self.handlers[name] = callback

    def set_expanded(self, value):
        self.options["expanded"] = value
        if "notify::expanded" in self.handlers:
            self.handlers["notify::expanded"](self, None)

    def get_expanded(self):
        return self.options.get("expanded", False)

    def add(self, child):
        self.append(child)

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def set_child(self, child):
        for old in self.children:
            old.parent = None
        self.children.clear()
        if child is not None:
            self.append(child)

    def get_child(self):
        return self.get_first_child()


def _layout():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_source_details.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    def add_action(row, button):
        if not hasattr(row, "_cifereca_action_flow"):
            row._cifereca_action_flow = Node()
        holder = Node()
        holder.set_child(button)
        row._cifereca_action_flow.append(holder)
    env = dict(Gtk=NS(Expander=Node, Box=Node, Orientation=NS(VERTICAL=1)),
               sync_action_row=lambda row: row, add_sync_action=add_action)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), env)
    return env["add_sync_detail"], add_action


def test_secondary_actions_move_as_same_objects_and_expand_without_work():
    add, add_action = _layout()
    group, source = Node(), Node()
    group.add(source)
    events = []
    primary, preview, audit = [Node(sensitive=value) for value in (True, False, True)]
    audit.click = lambda: events.append("audit for original account")
    for button in (primary, preview, audit):
        add_action(source, button)
    first, second = Node(), Node()
    add(group, first, action=preview, source_row=source)
    add(group, second, action=audit, source_row=source)
    tools = group._cifereca_sync_tools
    assert group.children == [source, tools]
    assert tools.options["expanded"] is False
    assert tools.get_child().children == [first, second]
    assert source._cifereca_action_flow.children[0].get_child() is primary
    assert len(source._cifereca_action_flow.children) == 1
    assert first._cifereca_action_flow.children[0].get_child() is preview
    assert second._cifereca_action_flow.children[0].get_child() is audit
    assert preview.options["sensitive"] is False
    tools.set_expanded(True)
    tools.set_expanded(False)
    assert events == []
    audit.click()
    assert events == ["audit for original account"]


def test_each_account_owns_its_section_and_main_errors_stay_outside():
    add, _ = _layout()
    groups = [Node(), Node()]
    for group in groups:
        source, diagnostic, tool = Node(), Node(), Node()
        group.add(source)
        group.add(diagnostic)
        add(group, tool)
        assert group.children[:2] == [source, diagnostic]
        assert group._cifereca_sync_tools.get_child().children == [tool]
    assert groups[0]._cifereca_sync_tools is not groups[1]._cifereca_sync_tools


def test_controller_keeps_durable_numilog_backup_status_outside_tools(monkeypatch):
    methods = callbacks._methods()
    details = []
    def record(group, row, **kw):
        details.append(row.title)
        group.add(row)
    # These are the actual controller function globals, not a parallel model.
    monkeypatch.setitem(methods, "add_sync_detail", record)
    window, broker, calls, _build, _page = callbacks._fixture(sources=[
        callbacks._source(provider_id="numilog", source_id="numilog",
                          sync_capabilities=("remote_inventory",)),
    ])
    methods["_refresh_sync_page"](window)
    assert "Aperçu local des achats Numilog" in details
    assert "Sauvegarde automatique des achats Numilog" not in details
    assert window.runner.tasks == []
    assert not any(call[0] in {"sync-source", "acquire", "prepare"} for call in calls)
    assert "1 source(s)" in window._sync_summary.label


def test_open_state_survives_refresh_only_for_the_same_identity():
    window, broker, _calls, _build, _page = callbacks._fixture()
    state = window._sync_tools_state
    state["kobo-one"] = True
    state["removed-account"] = True
    window._refresh_sync_page()
    assert window._sync_tools_state == {"kobo-one": True}
    window.service.current_identity = NS(identity_id="bob", label="Bob")
    broker.sources = lambda iid: [callbacks._source(identity_id=iid)]
    window._refresh_sync_page()
    assert window._sync_tools_state == {"kobo-one": False}
    # Old detached expanders retain the old map, not the new account's map.
    state["kobo-one"] = True
    assert window._sync_tools_state == {"kobo-one": False}
