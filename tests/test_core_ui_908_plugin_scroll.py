"""Deterministic regression for plugin manager scroll restoration (#908)."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


class Adjustment:
    def __init__(self, value=0, upper=3000, page=400):
        self.value = value
        self.upper = upper
        self.page = page
        self.history = []

    def get_value(self):
        return self.value

    def get_lower(self):
        return 0

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page

    def set_value(self, value):
        self.history.append(value)
        self.value = value


class Row:
    def __init__(self, y):
        self.y = y

    def compute_bounds(self, parent):
        return True, SimpleNamespace(get_y=lambda: self.y)


class Switch:
    def __init__(self):
        self.focus_calls = 0

    def grab_focus(self):
        self.focus_calls += 1


def _host(adjustment, rows=None, switches=None):
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    owner = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                 and x.name == "PluginManagerPage")
    fn = next(x for x in owner.body if isinstance(x, ast.FunctionDef)
              and x.name == "_restore_viewport")
    program = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn],
        type_ignores=[],
    ))
    idle, timer = [], []
    glib = SimpleNamespace(idle_add=lambda cb: idle.append(cb),
                           timeout_add=lambda delay, cb: timer.append((delay, cb)))
    scope = {"GLib": glib}
    exec(compile(program, str(PAGE), "exec"), scope)
    obj = SimpleNamespace(
        _plugin_widgets=rows or {}, _plugin_switches=switches or {},
        _viewport_token=None, outer=object(),
        get_root=lambda: object(), get_vadjustment=lambda: adjustment,
    )
    obj._restore_viewport = scope["_restore_viewport"].__get__(obj)
    return obj, idle, timer


def test_restore_after_rebuild_anchors_the_toggled_plugin_and_restores_keyboard_focus():
    adjustment = Adjustment()
    row = Row(1300)
    switch = Switch()
    host, idle, timer = _host(adjustment, {"provider.foo": row}, {"provider.foo": switch})
    host._restore_viewport(900, "provider.foo", -100, True)
    assert adjustment.history == []  # never restore against the temporarily empty tree
    idle.pop()()
    assert adjustment.value == 1400
    assert switch.focus_calls == 1
    row.y += 75  # wrapping/expander height changes after the first GTK layout
    delay, cb = timer.pop()
    assert delay > 0
    cb()
    assert adjustment.value == 1475
    assert switch.focus_calls == 1


def test_removed_filtered_plugin_falls_back_to_clamped_previous_scroll():
    adjustment = Adjustment(upper=600, page=400)
    host, idle, timer = _host(adjustment)
    host._restore_viewport(725, "provider.removed", 15, False)
    idle.pop()()
    assert adjustment.value == 200
    timer.pop()[1]()
    assert adjustment.value == 200


def test_stale_idle_and_timeout_cannot_override_newer_refresh():
    adjustment = Adjustment()
    host, idle, timer = _host(adjustment)
    host._restore_viewport(100, None, None, False)
    host._restore_viewport(320, None, None, False)
    idle.pop(0)()
    assert adjustment.history == []
    idle.pop(0)()
    assert adjustment.value == 320
    timer.pop(0)[1]()
    assert adjustment.value == 320


def test_activation_routes_anchor_through_refresh_without_changing_plugin_rules():
    source = PAGE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                 and x.name == "PluginManagerPage")
    methods = {fn.name: ast.get_source_segment(source, fn)
               for fn in owner.body if isinstance(fn, ast.FunctionDef)}
    assert "self._refresh_anchor_id = plugin_id" in methods["_apply_toggle"]
    assert "self.refresh(); self.on_changed()" in methods["_apply_toggle"]
    assert "self._plugin_widgets[d.id] = box" in methods["_plugin_row"]
    assert "self._plugin_switches[d.id] = toggle" in methods["_plugin_row"]
    assert "self._plugin_widgets.clear()" in methods["_render_inventory"]
