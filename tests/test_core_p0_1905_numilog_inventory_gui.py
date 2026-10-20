"""Numilog inventory consent and GTK scheduling, without provider/file I/O."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as startup
from cifereca.gui import source_inventory_timer as timer
from tests.test_core_p0_1346_inventory_schedule_ui import fixture
from tests.test_core_p0_1346_source_inventory_timer import rig, SOURCE_ID
from tests.test_core_p0_1347_kobo_confirmed_per_account import Dialog
from tests.test_core_ui_1347_sync_action_scope import _source


def _sources():
    return [_source(
        provider_id="numilog", source_id=f"numilog-{i}", label="Mes achats",
        sync_capabilities=("remote_inventory", "entitlements"),
    ) for i in (1, 2)]


def _control(window, index=0, *, source_id=None):
    groups = window._sync_sources_box.children
    if source_id is None:
        group = groups[index]
    else:
        matched = [group for group in groups
                   if group._cifereca_sync_source_id == source_id]
        assert len(matched) == 1, "Inventory policy must target exactly one account"
        group = matched[0]
    assert len(group.children) == 4  # source, preview, backup consent, inventory
    backup = group.children[2]
    assert backup.title == "Sauvegarde automatique des achats Numilog"
    assert "Sauvegarde planifiée indisponible" in backup.subtitle
    assert len(backup.suffixes) == 1 and not backup.suffixes[0].sensitive
    row = group.children[3]
    assert row.title == "Actualisation automatique des notices Numilog"
    return row.suffixes[0], row


@pytest.mark.parametrize("choice", ["on_start", "periodic"])
def test_explicit_numilog_policy_targets_only_one_account_without_acquisition(monkeypatch, choice):
    w, _, calls, modes, downloads = fixture(monkeypatch, sources=_sources())
    button, row = _control(w, 1)
    assert "Mode manuel" in row.subtitle and modes == downloads == {}
    assert not w.runner.tasks
    button.click()
    assert Dialog.shown[-1].title == "Planifier les notices Numilog ?"
    assert "sans téléchargement" in Dialog.shown[-1].body
    Dialog.shown[-1].choose(choice)
    assert modes == {"numilog-2": {"mode": choice, "interval_minutes": 60}}
    assert downloads == {} and w.runner.tasks == []
    assert not any(c[0] in {"prepare", "acquire", "sync-source", "schedule-write"} for c in calls)
    assert "Mode manuel" in _control(w)[1].subtitle


def test_numilog_policy_revocation_and_stale_identity_callbacks(monkeypatch):
    w, _, calls, modes, downloads = fixture(monkeypatch, sources=_sources())
    _control(w)[0].click()
    w.service.current_identity = NS(identity_id="bob")
    Dialog.shown[-1].choose("periodic")
    assert modes == {} and downloads == {}
    assert not any(c[0] == "inventory-write" for c in calls)


def test_disconnected_numilog_can_revoke_existing_inventory_consent(monkeypatch):
    sources = _sources()
    sources[0].connected = sources[0].enabled = False
    w, _, calls, modes, downloads = fixture(
        monkeypatch, sources=sources,
        inventory={"numilog-1": {"mode": "periodic", "interval_minutes": 30}},
    )
    # The still-connected second account now precedes the disabled first.
    assert w._sync_sources_box.children[0]._cifereca_sync_source_id == "numilog-2"
    button, row = _control(w, source_id="numilog-1")
    assert button.label == "Désactiver les actualisations"
    assert "périodique autorisée" in row.subtitle
    assert button.sensitive
    button.click()
    assert modes["numilog-1"] == {"mode": "manual", "interval_minutes": 30}
    assert Dialog.shown == [] and downloads == {} and not w.runner.tasks


def _timer_rig(mode):
    source = NS(
        identity_id="alice", source_id=SOURCE_ID, provider_id="numilog",
        enabled=True, connected=True, sync_capable=True, status="idle",
        phase_states=(NS(phase="discover_rights", status="idle", last_attempt=""),),
    )
    return rig(accounts=[source], modes={SOURCE_ID: {"mode": mode, "interval_minutes": 60}})


def test_numilog_startup_and_periodic_use_independent_real_entrypoints():
    r = _timer_rig("on_start")
    r.configure()
    assert not r.records["timers"]
    assert startup.dispatch_after_opening(r.window)
    assert not startup.dispatch_after_opening(r.window)
    assert not any(c[0] == "dispatch" for c in r.records["calls"])
    r.jobs[0].work()
    assert ("dispatch", "alice", "startup") in r.records["calls"]
    r = _timer_rig("periodic")
    assert not startup.dispatch_after_opening(r.window)
    r.configure()
    assert len(r.records["timers"]) == 1
    assert next(iter(r.records["timers"].values()))()
    r.jobs[0].work()
    assert ("dispatch", "alice", "periodic") in r.records["calls"]
    r.policies[SOURCE_ID] = {"mode": "manual", "interval_minutes": 60}
    r.listeners[0]()
    r.records["idle"].pop()()
    assert not r.records["timers"]


@pytest.mark.parametrize("mutation", ["disabled", "disconnected", "phase", "manual", "identity"])
def test_numilog_revoked_or_malformed_startup_admission_never_dispatches(mutation):
    r = _timer_rig("on_start")
    row = r.broker.sources("alice")[0]
    if mutation == "disabled": row.enabled = False
    if mutation == "disconnected": row.connected = False
    if mutation == "phase": row.phase_states = ()
    if mutation == "manual": r.policies[SOURCE_ID]["mode"] = "manual"
    if mutation == "identity": row.identity_id = "bob"
    assert not startup.dispatch_after_opening(r.window)
    assert not r.jobs


def test_numilog_periodic_requires_inventory_phase_evidence():
    r = _timer_rig("periodic")
    row = r.broker.sources("alice")[0]
    row.phase_states = ()
    assert timer.periodic_identities_due(r.window, r.broker) == ()
