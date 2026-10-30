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
    assert job.options["coalesce_key"] == "source:inventory:startup"
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
    if change == "foreign_provider":
        row.provider_id = "paris"
        row.sync_capabilities = ()  # Broker does not advertise this for Paris.
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
    # The generic timer fixture only records successful callbacks. Opening
    # error handling needs the actual failure callback as well.
    def submit(label, work, done=None, error=None, **options):
        r.jobs.append(NS(
            label=label, work=work, done=done, error=error, options=options,
        ))
    r.window.runner.submit = submit
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


def test_opt_in_revoked_while_startup_task_queued_never_reaches_broker():
    r = _rig()
    assert startup.dispatch_after_opening(r.window)
    r.policies[SOURCE_ID] = {"mode": "manual", "interval_minutes": 60}
    r.jobs[0].work()
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_queued_startup_rechecks_phase_clock_before_each_provider_attempt():
    r = _rig()
    assert startup.dispatch_after_opening(r.window)
    row = r.broker.sources("alice")[0]
    row.phase_states = (_phase(status="running"),)
    r.jobs[0].work()
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_startup_worker_redacts_provider_exception_without_leaking_secret():
    r = _rig()
    assert startup.dispatch_after_opening(r.window)
    r.broker.synchronize_scheduled_inventory = lambda *args: (
        _ for _ in ()
    ).throw(RuntimeError("https://private.invalid/?token=TOP_SECRET"))
    with pytest.raises(RuntimeError, match="Actualisation initiale des notices des comptes indisponible") as caught:
        r.jobs[0].work()
    assert "TOP_SECRET" not in str(caught.value)


def test_duplicate_configured_identity_blocks_all_startup_dispatch():
    r = _rig()
    r.window.service.identities.append(NS(identity_id="alice"))
    assert not startup.dispatch_after_opening(r.window)
    assert r.jobs == []


def test_reconnect_even_after_prior_noop_is_not_another_application_start():
    r, events, run_opening = _startup_window()
    r.window._source_inventory_startup_connection_seen = True
    posted = []
    startup_coordinator.connect_configured_sources(
        r.window, idle_add=posted.append,
    )
    r.jobs.pop().done({"connected": [], "errors": {}})
    assert len(posted) == 1
    assert posted.pop()() is False
    opening = r.jobs.pop()
    assert opening.options["kind"] == "opening-sync"
    opening.work(lambda *_a: None)
    opening.done({"identities": []})
    assert r.jobs == []
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_failed_first_account_is_redacted_and_second_account_still_runs():
    sources = [
        NS(identity_id="alice", source_id=SOURCE_ID, provider_id="kobo",
           connected=True, enabled=True, sync_capable=True,
           status="idle", last_attempt="", phase_states=(_phase(),)),
        NS(identity_id="bob", source_id="sync:" + "b" * 24, provider_id="kobo",
           connected=True, enabled=True, sync_capable=True,
           status="idle", last_attempt="", phase_states=(_phase(),)),
    ]
    r = _rig(accounts=sources)
    def dispatch(iid, trigger):
        r.records["calls"].append(("attempt", iid, trigger))
        if iid == "alice":
            return NS(ok=False, facts={"private": "SECRET"})
        return NS(ok=True)
    r.broker.synchronize_scheduled_inventory = dispatch
    assert startup.dispatch_after_opening(r.window)
    with pytest.raises(RuntimeError, match="Actualisation initiale des notices des comptes indisponible") as exc:
        r.jobs[0].work()
    assert "SECRET" not in str(exc.value)
    assert [
        c for c in r.records["calls"] if c[0] == "attempt"
    ] == [
        ("attempt", "alice", "startup"),
        ("attempt", "bob", "startup"),
    ]


@pytest.mark.parametrize("corruption", [
    "unhashable_provider", "unhashable_inventory_status",
    "policy_exception", "bad_phase_accessor",
])
def test_bad_first_source_does_not_starve_same_identity_on_start_inventory(
    corruption,
):
    bad = NS(
        identity_id="alice", source_id="sync:" + "b" * 24,
        provider_id="numilog", enabled=True, connected=True, sync_capable=True,
        status="idle", last_attempt="", phase_states=(_phase(),),
    )
    healthy = NS(
        identity_id="alice", source_id="sync:" + "c" * 24,
        provider_id="numilog", enabled=True, connected=True, sync_capable=True,
        status="idle", last_attempt="", phase_states=(_phase(),),
    )
    r = _rig(accounts=[bad, healthy])
    if corruption == "unhashable_provider":
        bad.provider_id = {"error": "NEVER_ECHO_PRIVATE"}
    elif corruption == "unhashable_inventory_status":
        bad.phase_states = (_phase(status=["NEVER_ECHO_PRIVATE"]),)
    elif corruption == "policy_exception":
        original = r.broker.inventory_refresh_schedule
        def fail_one(sid):
            if sid == bad.source_id:
                raise RuntimeError(
                    "https://private.invalid/?auth=NEVER_ECHO_PRIVATE"
                )
            return original(sid)
        r.broker.inventory_refresh_schedule = fail_one
    else:
        class InvalidPhase:
            @property
            def phase(self):
                raise RuntimeError("NEVER_ECHO_PRIVATE")
        bad.phase_states = (InvalidPhase(),)

    assert startup.due_startup_identities(r.window, r.broker) == ("alice",)
    assert startup.dispatch_after_opening(r.window) is True
    assert len(r.jobs) == 1
    assert r.jobs[0].options["kind"] == "source-sync"
    assert not any(entry[0] == "dispatch" for entry in r.records["calls"])
    r.jobs[0].work()
    assert ("dispatch", "alice", "startup") in r.records["calls"]
    assert "NEVER_ECHO_PRIVATE" not in repr(r.jobs)


def test_unknown_provider_startup_is_broker_capability_driven():
    row = NS(
        identity_id="alice", source_id=SOURCE_ID, provider_id="fixture-provider",
        sync_capabilities=("scheduled_refresh",), enabled=True, connected=True,
        sync_capable=True, status="idle", last_attempt="",
        phase_states=(_phase(),),
    )
    r = _rig(accounts=[row])
    assert startup.due_startup_identities(r.window, r.broker) == ("alice",)
    assert startup.dispatch_after_opening(r.window)
    r.jobs[0].work()
    assert ("dispatch", "alice", "startup") in r.records["calls"]
    row.sync_capabilities = ()
    assert startup.due_startup_identities(r.window, r.broker) == ()
