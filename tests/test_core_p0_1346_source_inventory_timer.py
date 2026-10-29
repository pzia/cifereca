"""P0 #1346: periodic Kobo inventory timer is consent-only and GTK-owned.

No Gtk runtime, provider HTTP, backing downloads or ADEPT are invoked.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_timer as timer

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
SOURCE_ID = "sync:" + "a" * 24


def rig(*, modes=None, accounts=None):
    records = {"calls": [], "timers": {}, "idle": [], "cancelled": [], "next_id": 1}
    if accounts is None:
        accounts = [NS(
            identity_id="alice", source_id=SOURCE_ID, provider_id="kobo",
            connected=True, enabled=True, sync_capable=True,
            status="idle", last_attempt="",
        )]
    identities = [NS(identity_id=i) for i in sorted(set(r.identity_id for r in accounts))]
    policies = dict(modes or {})
    listeners = []
    def subscribe(callback):
        listeners.append(callback)
        def unsubscribe():
            listeners.remove(callback)
            records["calls"].append(("unsubscribe",))
        return unsubscribe
    def sources(iid):
        records["calls"].append(("sources", iid))
        return tuple(r for r in accounts if r.identity_id == iid)
    def read(sid):
        records["calls"].append(("read", sid))
        return policies.get(sid, {"mode": "manual", "interval_minutes": 60})
    def scheduled(identity_id, trigger):
        records["calls"].append(("dispatch", identity_id, trigger))
        return NS(ok=True)
    broker = NS(
        sources=sources,
        inventory_refresh_schedule=read,
        synchronize_scheduled_inventory=scheduled,
        subscribe=subscribe,
    )
    jobs = []
    def submit(label, work, done=None, err=None, **options):
        jobs.append(NS(label=label, work=work, done=done, options=options))
    window = NS(
        _closed=False, service=NS(identities=identities),
        plugin_manager=NS(broker=lambda name: broker if name == "sync" else None),
        runner=NS(submit=submit),
    )
    def timeout_add_seconds(seconds, cb):
        timer_id = records["next_id"]
        records["next_id"] += 1
        records["calls"].append(("timer", seconds))
        records["timers"][timer_id] = cb
        return timer_id
    def source_remove(timer_id):
        records["cancelled"].append(timer_id)
        records["timers"].pop(timer_id, None)
    def idle_add(cb):
        records["idle"].append(cb)
    def configure():
        return timer.configure(
            window, timeout_add_seconds=timeout_add_seconds,
            source_remove=source_remove, idle_add=idle_add,
        )
    return NS(
        window=window, broker=broker, policies=policies, listeners=listeners,
        records=records, jobs=jobs, configure=configure,
        source_remove=source_remove,
    )


def test_manual_default_creates_subscription_but_no_timer_or_network():
    r = rig()
    assert r.configure() is False
    assert r.records["timers"] == {}
    assert len(r.listeners) == 1
    assert not r.jobs
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_periodic_opt_in_creates_single_minute_heartbeat_then_due_dispatch():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    assert len(r.records["timers"]) == 1
    assert ("timer", 60) in r.records["calls"]
    tick = next(iter(r.records["timers"].values()))
    assert tick() is True
    assert len(r.jobs) == 1
    job = r.jobs[0]
    assert job.options["kind"] == "source-sync"
    assert job.options["origin"] == "automatic"
    assert job.options["coalesce_key"] == "source:inventory:periodic"
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    job.work()
    assert ("dispatch", "alice", "periodic") in r.records["calls"]



def test_oct29_periodic_refresh_defers_while_automatic_opening_holds_lock():
    """The real 29 October opening took 86 s; the 60 s tick must not fail."""
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    opening = {"active": True}
    r.window.runner.is_active_automatic_opening = lambda: opening["active"]
    r.configure()
    tick = next(iter(r.records["timers"].values()))
    assert tick() is True
    assert r.jobs == []
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    opening["active"] = False
    assert tick() is True
    assert len(r.jobs) == 1
    r.jobs[0].work()
    assert ("dispatch", "alice", "periodic") in r.records["calls"]


def test_oct29_queued_periodic_worker_does_not_race_new_opening():
    """Recheck before broker I/O, not only when GTK enqueues the job."""
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    opening = {"active": False}
    r.window.runner.is_active_automatic_opening = lambda: opening["active"]
    r.configure()
    tick = next(iter(r.records["timers"].values()))
    assert tick() is True
    assert len(r.jobs) == 1
    opening["active"] = True
    r.jobs[0].work()  # Deferred, not a failed provider/Sync attempt.
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    opening["active"] = False
    assert tick() is True
    r.jobs[-1].work()
    assert r.records["calls"].count(("dispatch", "alice", "periodic")) == 1


def test_unavailable_opening_probe_defers_without_provider_side_effect():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    def unavailable():
        raise RuntimeError("PRIVATE_SESSION")
    r.window.runner.is_active_automatic_opening = unavailable
    r.configure()
    assert next(iter(r.records["timers"].values()))() is True
    assert not r.jobs



def test_on_start_policy_does_not_install_a_periodic_timer():
    r = rig(modes={SOURCE_ID: {"mode": "on_start", "interval_minutes": 60}})
    r.configure()
    assert r.records["timers"] == {}
    assert r.jobs == []


@pytest.mark.parametrize("failure", ["exception", "partial", "invalid_receipt"])
def test_periodic_failed_profile_does_not_starve_other_identities_or_claim_success(failure):
    accounts = [NS(
        identity_id=identity, source_id="sync:" + character * 24,
        provider_id="numilog" if identity == "bob" else "kobo",
        connected=True, enabled=True, sync_capable=True, status="idle",
        phase_states=(NS(phase="discover_rights", status="idle", last_attempt=""),),
    ) for identity, character in (("alice", "a"), ("bob", "b"), ("carol", "c"))]
    r = rig(accounts=accounts, modes={
        row.source_id: {"mode": "periodic", "interval_minutes": 15}
        for row in accounts
    })
    attempts = []
    def dispatch(identity, trigger):
        attempts.append((identity, trigger))
        if identity == "alice":
            if failure == "exception":
                raise ValueError("https://private.invalid/?auth=PRIVATE")
            if failure == "partial":
                return NS(ok=False, facts={"private": "PRIVATE"})
            return None
        return NS(ok=True)
    r.broker.synchronize_scheduled_inventory = dispatch
    r.configure()
    assert next(iter(r.records["timers"].values()))()
    assert len(r.jobs) == 1 and attempts == []
    with pytest.raises(RuntimeError, match="Actualisation des notices des comptes indisponible") as exc:
        r.jobs[0].work()
    assert attempts == [(identity, "periodic") for identity in ("alice", "bob", "carol")]
    assert "PRIVATE" not in str(exc.value)


def test_due_probe_uses_per_source_interval_and_running_status():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 30}})
    r.configure()
    account = r.broker.sources("alice")[0]
    account.last_attempt = (NOW - timedelta(minutes=29)).isoformat()
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ()
    account.last_attempt = (NOW - timedelta(minutes=31)).isoformat()
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ("alice",)
    account.status = "running"
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ()
    account.status = "idle"
    account.last_attempt = (NOW + timedelta(days=1)).isoformat()
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ()


@pytest.mark.parametrize("change", ["disabled", "disconnected", "unsupported", "wrong_provider", "wrong_identity"])
def test_source_revocation_or_mismatch_never_dispatches(change):
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    row = r.broker.sources("alice")[0]
    if change == "disabled": row.enabled = False
    if change == "disconnected": row.connected = False
    if change == "unsupported": row.sync_capable = False
    if change == "wrong_provider": row.provider_id = "paris"
    if change == "wrong_identity": row.identity_id = "bob"
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ()


@pytest.mark.parametrize("value", [
    {"mode": "periodic", "interval_minutes": True},
    {"mode": "periodic", "interval_minutes": 1},
    {"mode": "periodic", "interval_minutes": 10081},
    {"mode": "periodic", "interval_minutes": 15, "authorization": "secret"},
    {"mode": "periodic", "interval_minutes": "60"},
    {"mode": "https://secret.invalid/auth=token", "interval_minutes": 60},
    ["periodic"],
])
def test_malformed_policy_fails_closed_before_timer(value):
    r = rig(modes={SOURCE_ID: value})
    r.configure()
    assert r.records["timers"] == {}
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ()
    assert not r.jobs


def test_live_broker_changes_arm_and_disarm_timer_without_gtk_worker_access():
    r = rig()
    r.configure()
    callback = r.listeners[0]
    r.policies[SOURCE_ID] = {"mode": "periodic", "interval_minutes": 45}
    callback()  # may be sent from a worker thread
    assert r.records["timers"] == {}
    assert len(r.records["idle"]) == 1
    assert r.records["idle"].pop(0)() is False
    assert len(r.records["timers"]) == 1
    first = next(iter(r.records["timers"]))
    r.policies[SOURCE_ID] = {"mode": "manual", "interval_minutes": 45}
    callback()
    assert r.records["idle"].pop(0)() is False
    assert r.records["timers"] == {}
    assert first in r.records["cancelled"]
    assert len(r.listeners) == 1


def test_close_unsubscribes_and_stale_tick_or_idle_cannot_dispatch():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    tick = next(iter(r.records["timers"].values()))
    change = r.listeners[0]
    change()
    r.window._closed = True
    timer.stop(r.window, source_remove=r.source_remove)
    assert not r.records["timers"] and not r.listeners
    assert tick() is False
    assert r.records["idle"].pop(0)() is False
    assert r.jobs == []


def test_broker_replacement_never_dispatches_using_revoked_broker():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    old_tick = next(iter(r.records["timers"].values()))
    r.window.plugin_manager.broker = lambda _name: None
    assert old_tick() is False
    assert not r.records["timers"]
    assert not r.listeners and not r.jobs


def test_two_identities_remain_separate_even_in_one_due_poll():
    alice = NS(identity_id="alice", source_id=SOURCE_ID, provider_id="kobo",
               connected=True, enabled=True, sync_capable=True,
               status="idle", last_attempt="")
    bob = NS(identity_id="bob", source_id="sync:" + "b" * 24, provider_id="kobo",
             connected=True, enabled=True, sync_capable=True,
             status="idle", last_attempt="")
    policies = {
        alice.source_id: {"mode": "periodic", "interval_minutes": 60},
        bob.source_id: {"mode": "periodic", "interval_minutes": 60},
    }
    r = rig(accounts=[alice, bob], modes=policies)
    r.configure()
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ("alice", "bob")
    next(iter(r.records["timers"].values()))()
    r.jobs[0].work()
    assert [entry[1] for entry in r.records["calls"] if entry[0] == "dispatch"] == [
        "alice", "bob",
    ]


def test_provider_error_cannot_escape_automatic_worker():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    r.broker.synchronize_scheduled_inventory = lambda *_args: (
        _ for _ in ()
    ).throw(ValueError("https://secret.invalid/?token=PRIVATE"))
    next(iter(r.records["timers"].values()))()
    with pytest.raises(RuntimeError, match="Actualisation des notices des comptes indisponible") as exc:
        r.jobs[0].work()
    assert "PRIVATE" not in str(exc.value)


def test_ambiguous_identities_never_install_timer_or_enqueue_work():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.window.service.identities.append(NS(identity_id="alice"))
    r.configure()
    assert not r.records["timers"] and not r.jobs


def test_startup_connection_registers_timer_only_after_successful_broker_connect():
    from cifereca.gui.startup_coordinator import connect_configured_sources

    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 30}})
    posted, events = [], []
    r.window.service.connect_configured = lambda: {"connected": [], "errors": {}}
    r.window.service.last_loans = []
    r.window._populate_accounts = lambda: events.append("accounts")
    r.window._populate_loans = lambda rows: events.append("loans")
    r.window._activity_result = lambda *args, **kwargs: events.append("activity")
    r.window._render_dashboard = lambda: events.append("dashboard")
    r.window.run_opening_sync = lambda: events.append("opening-sync")
    assert connect_configured_sources(
        r.window, idle_add=posted.append,
        timeout_add_seconds=lambda seconds, tick: (
            r.records["timers"].update({99: tick}) or 99
        ),
        source_remove=r.source_remove,
    ) is False
    assert len(r.jobs) == 1 and posted == []
    r.jobs.pop().done({"connected": [], "errors": {}})
    assert len(posted) == 2
    assert r.records["timers"] == {}
    assert posted.pop(0)() is False
    assert events[-1] == "opening-sync"
    assert posted.pop(0)() is False
    assert list(r.records["timers"]) == [99]
    assert not any(c[0] == "dispatch" for c in r.records["calls"])


def test_window_close_always_releases_periodic_inventory_subscription():
    from cifereca.gui.window_close import close_window_resources

    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    first_timer_id = next(iter(r.records["timers"]))
    events = []
    r.window.config = NS(set=lambda *a: None, save=lambda: None)
    r.window.get_width = lambda: 1366
    r.window.get_height = lambda: 900
    r.window._cancel_notification_hide = lambda: None
    r.window._cancel_activity_hide = lambda: None
    r.window._cached_book_unsubscribe = lambda: None
    r.window.runner.close = lambda: events.append("runner")
    r.window.history_cover_resolver = NS(close=lambda: events.append("history"))
    r.window.covers = NS(close=lambda: events.append("covers"))
    r.window.service.close = lambda: events.append("service")
    assert close_window_resources(r.window, source_remove=r.source_remove) is False
    assert r.window._closed
    assert r.records["timers"] == {} and not r.listeners
    assert first_timer_id in r.records["cancelled"]
    assert events[-4:] == ["runner", "history", "covers", "service"]


def test_queued_periodic_work_is_discarded_after_broker_replacement():
    r = rig(modes={SOURCE_ID: {"mode": "periodic", "interval_minutes": 15}})
    r.configure()
    next(iter(r.records["timers"].values()))()
    assert len(r.jobs) == 1
    r.window.plugin_manager.broker = lambda _name: None
    assert r.jobs[0].work() is None
    assert not any(entry[0] == "dispatch" for entry in r.records["calls"])


@pytest.mark.parametrize("corrupt", [
    "unhashable_provider", "unhashable_status",
    "schedule_read_error", "malformed_phase",
])
def test_one_corrupt_source_cannot_starve_healthy_periodic_sibling(corrupt):
    bad_id = "sync:" + "b" * 24
    good_id = "sync:" + "c" * 24
    bad = NS(
        identity_id="alice", source_id=bad_id, provider_id="numilog",
        connected=True, enabled=True, sync_capable=True,
        status="idle", last_attempt="",
        phase_states=(NS(phase="discover_rights", status="idle", last_attempt=""),),
    )
    healthy = NS(
        identity_id="alice", source_id=good_id, provider_id="numilog",
        connected=True, enabled=True, sync_capable=True,
        status="idle", last_attempt="",
        phase_states=(NS(phase="discover_rights", status="idle", last_attempt=""),),
    )
    r = rig(
        accounts=[bad, healthy],
        modes={
            bad_id: {"mode": "periodic", "interval_minutes": 15},
            good_id: {"mode": "periodic", "interval_minutes": 15},
        },
    )
    if corrupt == "unhashable_provider":
        bad.provider_id = {"private": "DO_NOT_ECHO"}
    elif corrupt == "unhashable_status":
        bad.phase_states = (
            NS(phase="discover_rights", status={"private": "DO_NOT_ECHO"},
               last_attempt=""),
        )
    elif corrupt == "schedule_read_error":
        original = r.broker.inventory_refresh_schedule
        def broken(sid):
            if sid == bad_id:
                raise ValueError("https://private.invalid?auth=DO_NOT_ECHO")
            return original(sid)
        r.broker.inventory_refresh_schedule = broken
    else:
        bad.phase_states = (
            NS(phase="discover_rights", status="unknown", last_attempt=""),
        )

    # The source-specific corruption must not disable a whole identity's
    # opt-in heartbeat, even when corrupt source precedes a healthy one.
    assert r.configure() is False
    assert len(r.records["timers"]) == 1
    assert timer.periodic_identities_due(r.window, r.broker, now=NOW) == ("alice",)
    tick = next(iter(r.records["timers"].values()))
    assert tick() is True
    assert len(r.jobs) == 1
    r.jobs[0].work()
    assert ("dispatch", "alice", "periodic") in r.records["calls"]
    assert "DO_NOT_ECHO" not in repr(r.jobs)
