from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.plugin_view import plugin_dependents


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _status(identifier, *, required=(), recommended=(), enabled=True, package="suite"):
    descriptor = SimpleNamespace(
        id=identifier, display_name=identifier, package_id=package,
        requires=tuple(SimpleNamespace(plugin=plugin) for plugin in required),
        recommends=tuple(SimpleNamespace(plugin=plugin) for plugin in recommended),
    )
    return SimpleNamespace(descriptor=descriptor, enabled=enabled, state="ready")


class FakeDialog:
    def __init__(self, title, body):
        self.title, self.body = title, body
        self.responses = {}
        self.handlers = {}
        self.default = None
        self.close = None
        self.presented = False

    def add_response(self, identifier, label):
        self.responses[identifier] = label

    def set_response_appearance(self, *_):
        pass

    def set_default_response(self, name):
        self.default = name

    def set_close_response(self, name):
        self.close = name

    def connect(self, name, callback):
        self.handlers[name] = callback

    def present(self):
        self.presented = True

    def respond(self, response):
        self.handlers["response"](self, response)


def _host(statuses):
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "PluginManagerPage")
    required = {"_toggle", "_confirm_disable", "_apply_toggle"}
    methods = [node for node in cls.body
               if isinstance(node, ast.FunctionDef) and node.name in required]
    assert {m.name for m in methods} == required
    program = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *methods,
    ], type_ignores=[]))
    dialogs = []
    def make_dialog(_window, title, body):
        dialog = FakeDialog(title, body)
        dialogs.append(dialog)
        return dialog

    fake_adw = SimpleNamespace(
        MessageDialog=SimpleNamespace(new=make_dialog),
        ResponseAppearance=SimpleNamespace(DESTRUCTIVE="destructive"),
    )
    scope = {"plugin_dependents": plugin_dependents, "Adw": fake_adw}
    exec(compile(program, str(PAGE), "exec"), scope)
    host = type("TestPage", (), {name: scope[name] for name in required})()
    calls = []
    errors = []
    refreshed = []
    changed = []
    host.window = SimpleNamespace(show_error=lambda *args: errors.append(args))
    def set_enabled(identifier, enabled):
        calls.append((identifier, enabled))
        next(row for row in statuses if row.descriptor.id == identifier).enabled = enabled
    host.manager = SimpleNamespace(
        statuses=lambda: tuple(statuses), set_enabled=set_enabled,
    )
    host.refresh = lambda: refreshed.append(True)
    host.on_changed = lambda: changed.append(True)
    return host, dialogs, calls, errors, refreshed, changed


class FakeSwitch:
    def __init__(self, host, identifier, active=True):
        self.host, self.identifier, self.active = host, identifier, active
        self.events = 0

    def set_active(self, active):
        if self.active != active:
            self.active = active
            self.events += 1
            # GTK notifies on both the user's click and programmatic reset.
            self.host._toggle(self.identifier, active, self)

    def click_off(self):
        self.set_active(False)


def test_disable_parent_with_two_levels_requires_explicit_confirmation():
    statuses = [
        _status("main"),
        _status("source", required=("main",), package="sources"),
        _status("extension", required=("source",), package="extensions"),
    ]
    host, dialogs, calls, errors, refreshed, changed = _host(statuses)
    switch = FakeSwitch(host, "main")
    switch.click_off()
    assert switch.active is True
    assert switch.events == 2
    assert calls == [] and errors == [] and refreshed == []
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.presented is True
    assert dialog.default == dialog.close == "cancel"
    assert "2 extension(s)" in dialog.body
    assert "\n• source (sources)" in dialog.body
    assert "\\n" not in dialog.body
    assert "source (sources)" in dialog.body
    assert "extension (extensions)" in dialog.body
    assert "bloquées" in dialog.body
    dialog.respond("cancel")
    assert calls == [] and changed == []
    assert all(row.enabled for row in statuses)
    # Retrying after cancellation must still require confirmation, then write once.
    switch.click_off()
    assert len(dialogs) == 2
    assert calls == []
    dialogs[1].respond("disable")
    assert calls == [("main", False)]


def test_confirm_disables_parent_exactly_once_without_disabling_child_preferences():
    statuses = [_status("root"), _status("child", required=("root",))]
    host, dialogs, calls, errors, refreshed, changed = _host(statuses)
    switch = FakeSwitch(host, "root")
    switch.click_off()
    dialogs[0].respond("disable")
    assert calls == [("root", False)]
    assert refreshed == [True] and changed == [True]
    assert errors == []
    assert statuses[0].enabled is False and statuses[1].enabled is True


def test_disabling_leaf_and_enabling_plugin_preserves_fast_path():
    statuses = [_status("parent"), _status("child", required=("parent",)), _status("off", enabled=False)]
    host, dialogs, calls, errors, refreshed, changed = _host(statuses)
    FakeSwitch(host, "child").click_off()
    host._toggle("off", True)
    assert dialogs == []
    assert calls == [("child", False), ("off", True)]
    assert len(refreshed) == len(changed) == 2 and errors == []


def test_recommended_and_inactive_extensions_do_not_trigger_confirmation():
    statuses = [
        _status("root"), _status("optional", recommended=("root",)),
        _status("inactive", required=("root",), enabled=False),
    ]
    host, dialogs, calls, _, _, _ = _host(statuses)
    FakeSwitch(host, "root").click_off()
    assert dialogs == []
    assert calls == [("root", False)]


def test_large_dependency_set_is_bounded_but_counted():
    statuses = [_status("root")] + [
        _status(f"child-{i:02d}", required=("root",), package=f"package-{i:02d}")
        for i in range(35)
    ]
    host, dialogs, calls, _, _, _ = _host(statuses)
    FakeSwitch(host, "root").click_off()
    assert calls == []
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert "35 extension(s)" in dialog.body
    assert "et 29 autre(s)" in dialog.body
    assert dialog.body.count("•") == 6


def test_activation_switch_passes_widget_to_toggle_for_restoration():
    source = PAGE.read_text(encoding="utf-8")
    assert 'self._toggle(pid, sw.get_active(), sw)' in source
    assert 'self._restoring_activation_switch = True' in source
    assert 'self._restoring_activation_switch = False' in source
