"""CoreUI canonical consent: explicit confirmation and stale identity checks."""
from __future__ import annotations
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from cifereca.gui.sync_action_scope import capture_sync_action_scope, sync_action_scope_current
from tests.test_core_ui_1347_sync_action_scope import Widget, _fixture, _source

class Dialog:
    def __init__(self, **kw):
        self.kw, self.callbacks = kw, {}
    def add_response(self, *args): pass
    def set_response_appearance(self, *args): pass
    def connect(self, signal, cb): self.callbacks[signal] = cb
    def present(self): pass
    def respond(self, code): self.callbacks["response"](self, code)

def rig(*, allowed=False, eligible=True, malformed=False):
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/canonical_consent_controls.py"
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef)
                and n.name == "append_canonical_consent_controls")
    future = ast.ImportFrom(module="__future__",
                            names=[ast.alias(name="annotations")], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, node], type_ignores=[]))
    adw = NS(ActionRow=Widget, ResponseAppearance=NS(SUGGESTED=1, DESTRUCTIVE=2))
    dialogs = []
    adw.MessageDialog = lambda **kw: (dialogs.append(Dialog(**kw)) or dialogs[-1])
    env = dict(Adw=adw, Gtk=NS(Align=NS(CENTER=1)),
               sync_action_scope_current=sync_action_scope_current,
               add_sync_action=lambda row, button: row.add_suffix(button),
               add_sync_detail=lambda group, row: group.add(row))
    exec(compile(tree, str(path), "exec"), env)
    source = _source(identity_id="alice", source_id="kobo-one",
                     provider_id="kobo", enabled=True)
    window, broker, _calls, _build, _page = _fixture(sources=[source])
    choices = {"reference": "a" * 64, "kind": "purchase",
               "allowed": allowed, "eligible": eligible}
    if malformed: choices["secret"] = "PRIVATE"
    broker.canonical_acquisition_choices = lambda _sid, *, page=0: (choices,)
    changed = []
    broker.set_canonical_acquisition_choice = (
        lambda sid, token, *, allowed: changed.append((sid, token, allowed))
    )
    scope = capture_sync_action_scope(
        window, page_token=window._sync_page_token,
        preview_generation=window._sync_preview_generation,
    )
    group = Widget()
    count = env["append_canonical_consent_controls"](
        window, broker, source, scope, group,
    )
    return window, dialogs, group, count, changed

def test_grant_requires_explicit_confirm_and_has_no_network():
    w, dialogs, group, count, changed = rig()
    assert count == 1 and "Achat n°1" in group.children[0].title
    group.children[0].suffixes[0].click()
    assert not changed
    dialogs[0].respond("cancel")
    assert not changed
    group.children[0].suffixes[0].click()
    dialogs[-1].respond("confirm")
    assert changed == [("kobo-one", "a" * 64, True)]
    assert w.runner.tasks == []

def test_revoke_inactive_prior_grant_needs_confirm():
    w, dialogs, group, count, changed = rig(allowed=True, eligible=False)
    group.children[0].suffixes[0].click()
    assert not changed
    dialogs[0].respond("confirm")
    assert changed == [("kobo-one", "a" * 64, False)]

@pytest.mark.parametrize("change", ["profile", "page", "closed"])
def test_stale_scope_never_grants(change):
    w, dialogs, group, _count, changed = rig()
    group.children[0].suffixes[0].click()
    if change == "profile": w.service.current_identity = NS(identity_id="bob")
    elif change == "page": w._refresh_sync_page()
    else: w._closed = True
    dialogs[0].respond("confirm")
    assert changed == []

def test_malformed_or_ineligible_right_is_not_authorizable():
    w, dialogs, group, count, changed = rig(eligible=False)
    assert count == 1 and group.children[0].suffixes[0].sensitive is False
    _w, _d, malformed, count, _changed = rig(malformed=True)
    assert count == 0 and malformed.children == []
