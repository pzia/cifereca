"""#3416: independent, capability-driven remote inventory schedule in Sync.

Runs the actual CoreUI function via AST with headless GTK doubles.
No provider network, SQLite, file write or acquisition takes place.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import sys

import pytest

from cifereca.gui.sync_action_scope import capture_sync_action_scope, sync_action_scope_current
from tests.test_core_ui_1347_sync_action_scope import Widget, _fixture, _source
from tests.test_core_p0_3416_source_sync_consent_ui import renderer as consent_renderer


class Dialog:
    def __init__(self):
        self.responses = {}
        self.handler = None
        self.presented = False

    def add_response(self, key, label): self.responses[key] = label
    def set_response_appearance(self, *args): pass
    def connect(self, signal, callback):
        assert signal == "response"
        self.handler = callback
    def present(self): self.presented = True
    def respond(self, choice):
        assert self.handler is not None
        self.handler(self, choice)


def renderer(*, dialogs):
    path = Path(__file__).resolve().parents[1] / (
        "src/cifereca/gui/source_inventory_schedule_controls.py"
    )
    fn = next(
        node for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef)
    )
    tree = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__",
                       names=[ast.alias(name="annotations")], level=0),
        fn,
    ], type_ignores=[]))
    def new_dialog(*args):
        d = Dialog()
        dialogs.append((args, d))
        return d
    env = {
        "Gtk": NS(),
        "Adw": NS(
            ActionRow=Widget, MessageDialog=NS(new=new_dialog),
            ResponseAppearance=NS(SUGGESTED=1),
        ),
        "sync_action_scope_current": sync_action_scope_current,
        "sync_action_row": lambda row: row,
        "add_sync_action": lambda row, button: row.add_suffix(button),
    }
    exec(compile(tree, str(path), "exec"), env)
    return env["append_source_inventory_schedule_controls"]


def rig(*, provider="provider.future", mode="manual", interval=60,
        enabled=True, connected=True, cap=True):
    window, broker, calls, _build, _page = _fixture(sources=[])
    source = _source(
        provider_id=provider, identity_id="alice", source_id="exact-route",
        sync_capabilities=("remote_inventory", "scheduled_refresh") if cap else (),
        enabled=enabled, connected=connected, sync_capable=True,
    )
    state = {"mode": mode, "interval_minutes": interval}
    reads, writes, dialogs = [], [], []
    def read(sid):
        reads.append(sid)
        return dict(state)
    def write(sid, new_mode, *, interval_minutes):
        writes.append((sid, new_mode, interval_minutes))
        state["mode"] = new_mode
        return dict(state)
    broker.inventory_refresh_schedule = read
    broker.set_inventory_refresh_schedule = write
    window._refresh_sync_page = lambda: setattr(
        window, "_sync_preview_generation", window._sync_preview_generation + 1,
    )
    scope = capture_sync_action_scope(
        window, page_token=window._sync_page_token,
        preview_generation=window._sync_preview_generation,
    )
    group = Widget()
    fn = renderer(dialogs=dialogs)
    return fn, window, broker, source, scope, group, state, reads, writes, dialogs


@pytest.mark.parametrize("provider", [
    "provider.future", "kobo", "numilog", "paris", "bnf-pret",
])
def test_provider_neutral_manual_state_requires_explicit_confirmation(provider):
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig(provider=provider)
    fn(w, b, s, scope, group)
    row, = group.children
    assert row.title == "Actualisation automatique des notices et droits"
    assert "Les téléchargements sont réglés séparément" in row.subtitle
    assert [b.label for b in row.suffixes] == ["Au démarrage…", "Périodique…"]
    assert reads == ["exact-route"] and writes == [] and dialogs == []
    row.suffixes[0].click()
    assert writes == [] and len(dialogs) == 1
    title, msg = dialogs[0][0][1:]
    assert "notices et droits" in title
    assert "Aucun fichier EPUB/ACSM" in msg
    dialogs[0][1].respond("cancel")
    assert writes == [] and state["mode"] == "manual"
    row.suffixes[0].click()
    dialogs[-1][1].respond("confirm")
    assert writes == [("exact-route", "on_start", 60)]
    assert state["mode"] == "on_start"


def test_periodic_is_independent_of_download_consent_and_declares_scheduler():
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig()
    fn(w, b, s, scope, group)
    group.children[0].suffixes[1].click()
    dialogs[0][1].respond("confirm")
    assert writes == [("exact-route", "periodic", 60)]
    assert state["mode"] == "periodic"


@pytest.mark.parametrize("original", ["on_start", "periodic"])
def test_revocation_is_available_without_connection(original):
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig(
        mode=original, connected=False, enabled=False,
    )
    fn(w, b, s, scope, group)
    assert len(group.children) == 1
    row = group.children[0]
    assert "N'autorise aucun téléchargement" in row.subtitle
    assert [button.label for button in row.suffixes] == [
        "Désactiver les actualisations",
    ]
    row.suffixes[0].click()
    assert writes == [("exact-route", "manual", 60)]
    assert state["mode"] == "manual" and dialogs == []


@pytest.mark.parametrize("disqualify", ["disabled", "disconnected", "no_cap", "unsupported"])
def test_ineligible_source_never_receives_implicit_network_consent(disqualify):
    kwargs = {"enabled": False} if disqualify == "disabled" else (
        {"connected": False} if disqualify == "disconnected" else (
            {"cap": False} if disqualify == "no_cap" else {}
        )
    )
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig(**kwargs)
    if disqualify == "unsupported":
        b.inventory_refresh_schedule = lambda _sid: (
            (_ for _ in ()).throw(ValueError("not supported"))
        )
    fn(w, b, s, scope, group)
    if disqualify in {"no_cap", "unsupported"}:
        assert group.children == [] and writes == []
        return
    for button in group.children[0].suffixes:
        assert not button.sensitive
        button.click()
    assert writes == [] and dialogs == []


@pytest.mark.parametrize("mutation", ["identity", "page", "closed"])
def test_queued_confirmation_is_revoked_on_scope_change(mutation):
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig()
    fn(w, b, s, scope, group)
    group.children[0].suffixes[0].click()
    if mutation == "identity":
        w.service.current_identity = NS(identity_id="bob")
    elif mutation == "page":
        w._refresh_sync_page()
    else:
        w._closed = True
    dialogs[0][1].respond("confirm")
    assert writes == []


def test_stale_policy_is_not_overwritten_with_an_old_mode():
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig()
    fn(w, b, s, scope, group)
    group.children[0].suffixes[0].click()
    state["mode"] = "periodic"  # concurrent policy edit
    dialogs[0][1].respond("confirm")
    assert writes == []
    assert state["mode"] == "periodic"


@pytest.mark.parametrize("policy", [
    {}, {"mode": "manual"}, {"mode": "manual", "interval_minutes": True},
    {"mode": "manual", "interval_minutes": 1},
    {"mode": "manual", "interval_minutes": 60, "extra": "private"},
    {"mode": "invalid", "interval_minutes": 60},
    {"mode": [], "interval_minutes": 60},
    {"mode": {}, "interval_minutes": 60},
    {"mode": None, "interval_minutes": 60},
])
def test_malformed_policy_never_creates_an_action(policy):
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig()
    b.inventory_refresh_schedule = lambda sid: dict(policy)
    fn(w, b, s, scope, group)
    assert group.children == [] and writes == []


def test_failed_policy_update_is_generic_and_does_not_retry():
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig(mode="on_start")
    error = []
    w.show_error = lambda *args: error.append(args)
    def fail(*args, **kwargs):
        writes.append(args)
        raise ValueError("PRIVATE https://signed.invalid/?token=SECRET")
    b.set_inventory_refresh_schedule = fail
    fn(w, b, s, scope, group)
    group.children[0].suffixes[0].click()
    assert len(writes) == len(error) == 1
    assert "PRIVATE" not in str(error)
    assert state["mode"] == "on_start"


def test_remote_inventory_without_scheduled_refresh_never_exposes_fake_consent():
    fn, w, b, s, scope, group, state, reads, writes, dialogs = rig(cap=False)
    s.sync_capabilities = ("remote_inventory",)
    fn(w, b, s, scope, group)
    assert group.children == []
    assert reads == writes == dialogs == []


def test_modern_sync_page_shows_separate_inventory_and_file_consent(monkeypatch):
    module = ModuleType("cifereca.gui.source_inventory_schedule_controls")
    dialogs = []
    module.append_source_inventory_schedule_controls = renderer(dialogs=dialogs)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    module2 = ModuleType("cifereca.gui.source_sync_consent_controls")
    module2.append_source_sync_consent_controls = consent_renderer()
    monkeypatch.setitem(sys.modules, module2.__name__, module2)
    w, b, calls, _build, _page = _fixture(sources=[])
    source = _source(
        provider_id="provider.future", source_id="opaque-source",
        label="Bibliothèque future",
        sync_capabilities=("remote_inventory", "scheduled_refresh"),
    )
    b.sources = lambda iid: [source]
    b.source_sync_consent = lambda sid: dict(
        supported=True, configured=True, enabled=False,
        revision="rev-one", valid=True,
    )
    b.set_source_sync_consent = lambda *args, **kwargs: pytest.fail(
        "render must never modify download consent"
    )
    b.inventory_refresh_schedule = lambda sid: (
        calls.append(("inventory-read", sid))
        or {"mode": "manual", "interval_minutes": 60}
    )
    b.set_inventory_refresh_schedule = lambda *args, **kwargs: pytest.fail(
        "render must never modify inventory schedule"
    )
    w._refresh_sync_page()
    group, = w._sync_sources_box.children
    assert [child.title for child in group.children] == [
        "Bibliothèque future", "Téléchargement automatique de ce compte",
        "Actualisation automatique des notices et droits",
    ]
    assert "Désactivé" in group.children[1].subtitle
    assert "Manuelle" in group.children[2].subtitle
    assert ("inventory-read", "opaque-source") in calls
    assert w.runner.tasks == []
