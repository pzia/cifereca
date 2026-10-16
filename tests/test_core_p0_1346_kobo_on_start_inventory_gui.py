"""P0 #1346: startup on-start inventory is opt-in, post-opening, identity scoped.

The real GTK/Core coordinator functions run with headless doubles, no I/O,
ADEPT, materialization, background timer or real provider service.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as startup
from cifereca.gui import startup_coordinator
from tests.test_core_p0_1346_source_inventory_timer import rig, SOURCE_ID


def _phase(*, status="idle", last_attempt=""):
    return NS(
        phase="discover_rights", status=status,
        last_attempt=last_attempt, last_success="",
    )


def _rig(*, mode="on_start", accounts=None):
    if accounts is None:
        accounts = [
            NS(
                identity_id="alice", source_id=SOURCE_ID, provider_id="kobo",
                enabled=True, connected=True, sync_capable=True,
                status="idle", last_attempt="",
                phase_states=(_phase(),),
            ),
        ]
    policies = {
        row.source_id: {"mode": mode, "interval_minutes": 60}
        for row in accounts
    }
    return rig(modes=policies, accounts=accounts)


def test_on_start_is_queued_once_and_never_calls_provider_on_gtk_thread():
    r = _rig()
    assert startup.due_startup_identities(r.window, r.broker) == ("alice",)
    assert startup.dispatch_after_opening(r.window)
    assert not startup.dispatch_after_opening(r.window)
    assert len(r.jobs) == 1
    job = r.jobs[0]
    assert job.options["origin"] == "automatic"
    assert job.options["kind"] == "source-sync"
    assert job.options["coalesce_key"] == "kobo:inventory:startup"
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    job.work()
    assert [
        c for c in r.records["calls"] if c[0] == "dispatch"
    ] == [("dispatch", "alice", "startup")]


@pytest.mark.parametrize("mode", ["manual", "periodic"])
def test_startup_does_not_inherit_other_sync_or_download_policies(mode):
    r = _rig(mode=mode)
    assert startup.due_startup_identities(r.window, r.broker) == ()
    assert startup.dispatch_after_opening(r.window) is False
    assert r.jobs == []


@pytest.mark.parametrize("change", [
    "disabled", "disconnected", "not_sync_capable", "foreign_provider",
    "wrong_identity", "bad_status", "bad_clock", "missing_phase",
])
def test_bad_identity_or_phase_never_authorizes_startup_inventory(change):
    r = _rig()
    row = r.broker.sources("alice")[0]
    if change == "disabled": row.enabled = False
    if change == "disconnected": row.connected = False
    if change == "not_sync_capable": row.sync_capable = False
    if change == "foreign_provider": row.provider_id = "paris"
    if change == "wrong_identity": row.identity_id = "bob"
    if change == "bad_status": row.phase_states = (_phase(status="unknown"),)
    if change == "bad_clock":
        row.phase_states = (_phase(last_attempt="https://private.invalid?token=SECRET"),)
    if change == "missing_phase": row.phase_states = ()
    assert startup.dispatch_after_opening(r.window) is False
    assert r.jobs == []


def test_stale_queued_startup_work_is_dropped_after_window_close_or_broker_change():
    r = _rig()
    assert startup.dispatch_after_opening(r.window)
    r.window._closed = True
    assert r.jobs[0].work() is None
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    r.window._closed = False
    r.window.plugin_manager.broker = lambda _name: None
    assert r.jobs[0].work() is None
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_multi_identity_dispatch_never_falls_back_to_selected_account():
    sources = [
        NS(
            identity_id="alice", source_id=SOURCE_ID, provider_id="kobo",
            connected=True, enabled=True, sync_capable=True,
            status="idle", last_attempt="", phase_states=(_phase(),),
        ),
        NS(
            identity_id="bob", source_id="sync:" + "b" * 24, provider_id="kobo",
            connected=True, enabled=True, sync_capable=True,
            status="idle", last_attempt="", phase_states=(_phase(),),
        ),
    ]
    r = _rig(accounts=sources)
    r.window.service.current_identity = NS(identity_id="alice")
    assert startup.due_startup_identities(r.window, r.broker) == ("alice", "bob")
    assert startup.dispatch_after_opening(r.window)
    assert len(r.jobs) == 1
    r.jobs[0].work()
    assert [
        c for c in r.records["calls"] if c[0] == "dispatch"
    ] == [
        ("dispatch", "alice", "startup"), ("dispatch", "bob", "startup"),
    ]


def _opening_controller():
    """Compile the actual extracted GUI controller, no import gi needed."""
    owner = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "run_opening_sync"
    )
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")],
            level=0,
        ),
        fn,
    ], type_ignores=[]))
    globals_ = {
        "__package__": "cifereca.gui",
        "TaskPriority": NS(
            USER_ACTION=10, MAINTENANCE=20, ACTIVE_LOAN=30, PROVIDER_SYNC=40,
        ),
    }
    exec(compile(module, str(owner), "exec"), globals_)
    return globals_["run_opening_sync"]


def _startup_window():
    r = _rig()
    events = []
    r.window.service.last_loans = []
    r.window.service.connect_configured = lambda: {"connected": [], "errors": {}}
    r.window.service.opening_sync = lambda **kwargs: (
        events.append("opening-worker") or {"identities": []}
    )
    r.window._populate_accounts = lambda: events.append("accounts")
    r.window._populate_loans = lambda _rows: events.append("loans")
    r.window._render_dashboard = lambda: events.append("dashboard")
    r.window.refresh_library = lambda *_args: events.append("library")
    r.window.refresh_recent = lambda: events.append("recent")
    r.window._activity_result = lambda *a, **kw: events.append("activity")
    r.window.diagnostics = NS(add=lambda *a, **kw: events.append("diagnostic"))
    run_opening = _opening_controller()
    r.window.run_opening_sync = lambda: run_opening(r.window)
    return r, events, run_opening


def test_real_startup_callback_queues_opted_in_inventory_only_after_opening_completion():
    r, events, run_opening = _startup_window()
    queued = []
    assert startup_coordinator.connect_configured_sources(
        r.window, idle_add=queued.append,
    ) is False
    assert len(r.jobs) == 1
    assert queued == []
    connection = r.jobs.pop()
    connection.done({"connected": [], "errors": {}})
    assert len(queued) == 1
    assert queued.pop()() is False
    assert len(r.jobs) == 1
    assert r.window._source_inventory_startup_pending is False
    opening = r.jobs.pop()
    assert opening.options["kind"] == "opening-sync"
    assert events.count("opening-worker") == 0
    opening.work(lambda *_args, **_kw: None)
    assert events.count("opening-worker") == 1
    assert r.jobs == []
    opening.done({"identities": []})
    assert len(r.jobs) == 1
    task = r.jobs.pop()
    assert task.options["kind"] == "source-sync"
    assert task.options["origin"] == "automatic"
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    task.work()
    assert ("dispatch", "alice", "startup") in r.records["calls"]


def test_manual_and_deep_opening_never_reuse_auto_startup_consent():
    r, events, run_opening = _startup_window()
    run_opening(r.window, NS(label="manual"), deep=False)
    manual = r.jobs.pop()
    manual.work(lambda *_a: None)
    manual.done({"identities": []})
    assert r.jobs == []
    run_opening(r.window, deep=True)
    deep = r.jobs.pop()
    deep.work(lambda *_a: None)
    deep.done({"identities": []})
    assert r.jobs == []
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_failed_or_closed_opening_never_queues_inventory_without_lock_release():
    r, events, run_opening = _startup_window()
    r.window._source_inventory_startup_pending = True
    run_opening(r.window)
    r.window._source_inventory_startup_pending = False
    opening = r.jobs.pop()
    opening.error(RuntimeError("private provider error"))
    assert r.jobs == []
    r.window._source_inventory_startup_pending = True
    run_opening(r.window)
    r.window._source_inventory_startup_pending = False
    opening = r.jobs.pop()
    r.window._closed = True
    opening.done({"identities": []})
    assert r.jobs == []
