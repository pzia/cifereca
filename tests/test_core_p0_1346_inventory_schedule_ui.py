"""P0 #1346: Kobo inventory schedule is an independent, explicit GTK opt-in.

Actual sync_controller.py methods, rendered with strict headless GTK doubles.
No network, ADEPT, acquisition, timers or implicit policy migration.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1347_sync_action_scope as harness
from tests import test_core_p0_1346_kobo_schedule_ui as materialization_harness
from tests.test_core_p0_1347_kobo_confirmed_per_account import Dialog


def fixture(monkeypatch, *, sources=None, inventory=None):
    if sources is None:
        sources = [harness._source(
            sync_capabilities=("remote_inventory", "local_materialization"),
        )]
    w, broker, calls, materialization = materialization_harness.fixture(
        monkeypatch, sources=sources,
    )
    modes = dict(inventory or {})

    def read(sid):
        calls.append(("inventory-read", sid))
        return dict(modes.get(sid, {"mode": "manual", "interval_minutes": 60}))

    def write(sid, mode, *, interval_minutes=60):
        calls.append(("inventory-write", sid, mode, interval_minutes))
        assert mode in {"manual", "on_start", "periodic"}
        assert type(interval_minutes) is int
        policy = {"mode": mode, "interval_minutes": interval_minutes}
        modes[sid] = policy
        return dict(policy)

    broker.inventory_refresh_schedule = read
    broker.set_inventory_refresh_schedule = write
    w._refresh_sync_page()
    return w, broker, calls, modes, materialization


def controls(w, idx=0):
    group = w._sync_sources_box.children[idx]
    # Existing download actions and their separate consent row are stable.
    assert group.children[3].title == "Téléchargement local · confirmation obligatoire"
    assert group.children[4].title == "Téléchargement automatique de ce compte"
    row = group.children[5]
    assert row.title == "Actualisation automatique des notices Kobo"
    assert len(row.suffixes) == 1
    return row.suffixes[0], row


def test_manual_default_does_not_opt_in_or_issue_network_or_download(monkeypatch):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    button, row = controls(w)
    assert button.sensitive
    assert button.label == "Planifier les notices…"
    assert "Mode manuel" in row.subtitle
    assert "Aucun téléchargement" in row.subtitle
    w._refresh_sync_page()
    assert modes == {} and downloads == {}
    assert not any(c[0] in {"inventory-write", "schedule-write", "sync-source"} for c in calls)
    assert w.runner.tasks == []


@pytest.mark.parametrize("choice", ["on_start", "periodic"])
def test_explicit_gtk_dialog_persists_only_chosen_kobo_inventory_policy(monkeypatch, choice):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    button, row = controls(w)
    button.click()
    assert len(Dialog.shown) == 1
    dlg = Dialog.shown[0]
    assert dlg.presented
    assert "sans téléchargement" in dlg.body
    assert "compte" in dlg.body
    assert modes == {} and w.runner.tasks == []
    dlg.choose(choice)
    assert modes == {"kobo-one": {"mode": choice, "interval_minutes": 60}}
    assert ("inventory-write", "kobo-one", choice, 60) in calls
    assert downloads == {}
    assert not any(c[0] in {"schedule-write", "prepare", "acquire", "sync-source"} for c in calls)
    new, row = controls(w)
    assert new.label == "Désactiver les actualisations"
    assert "N'autorise aucun téléchargement" in row.subtitle


def test_cancel_does_not_enable_an_inventory_schedule(monkeypatch):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    controls(w)[0].click()
    Dialog.shown[-1].choose("cancel")
    assert modes == downloads == {}
    assert not any(c[0].endswith("-write") for c in calls)


def test_disable_is_immediate_and_allowed_for_disconnected_or_disabled_source(monkeypatch):
    src = harness._source(
        enabled=False, connected=False,
        sync_capabilities=("remote_inventory", "local_materialization"),
    )
    w, broker, calls, modes, downloads = fixture(
        monkeypatch, sources=[src],
        inventory={"kobo-one": {"mode": "periodic", "interval_minutes": 45}},
    )
    button, row = controls(w)
    assert button.sensitive
    assert "45 min" in row.subtitle
    button.click()
    assert modes["kobo-one"] == {"mode": "manual", "interval_minutes": 45}
    assert ("inventory-write", "kobo-one", "manual", 45) in calls
    assert Dialog.shown == []
    assert not w.runner.tasks


def test_two_identical_kobo_names_still_have_independent_schedule(monkeypatch):
    sources = [
        harness._source(
            source_id="one", label="Kobo identique",
            sync_capabilities=("remote_inventory", "local_materialization"),
        ),
        harness._source(
            source_id="two", label="Kobo identique",
            sync_capabilities=("remote_inventory", "local_materialization"),
        ),
    ]
    w, broker, calls, modes, downloads = fixture(monkeypatch, sources=sources)
    controls(w, 1)[0].click()
    assert "Kobo identique" in Dialog.shown[-1].body
    Dialog.shown[-1].choose("periodic")
    assert modes == {"two": {"mode": "periodic", "interval_minutes": 60}}
    assert controls(w, 0)[0].label == "Planifier les notices…"
    assert controls(w, 1)[0].label == "Désactiver les actualisations"


@pytest.mark.parametrize("enabled,connected,capability", [
    (False, True, True), (True, False, True), (True, True, False),
])
def test_noneligible_source_cannot_obtain_new_scheduled_consent(
    monkeypatch, enabled, connected, capability,
):
    src = harness._source(
        enabled=enabled, connected=connected,
        sync_capabilities=("remote_inventory", "local_materialization")
        if capability else ("local_materialization",),
    )
    w, broker, calls, modes, downloads = fixture(monkeypatch, sources=[src])
    button, _row = controls(w)
    assert not button.sensitive
    button.click()
    assert modes == downloads == {}
    assert Dialog.shown == []
    assert not any(c[0].endswith("-write") for c in calls)


def test_old_dialog_is_revoked_after_page_refresh_or_identity_aba(monkeypatch):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    w._identity_context_generation = 7
    w._refresh_sync_page()
    controls(w)[0].click()
    w._identity_context_generation = 9
    Dialog.shown[-1].choose("periodic")
    assert modes == {}
    w._refresh_sync_page()
    controls(w)[0].click()
    w._refresh_sync_page()
    Dialog.shown[-1].choose("on_start")
    assert modes == {}
    assert not any(c[0] == "inventory-write" for c in calls)


def test_optimistic_policy_guard_does_not_overwrite_concurrent_update(monkeypatch):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    controls(w)[0].click()
    modes["kobo-one"] = {"mode": "periodic", "interval_minutes": 75}
    Dialog.shown[-1].choose("on_start")
    assert modes == {"kobo-one": {"mode": "periodic", "interval_minutes": 75}}
    assert not any(c[0] == "inventory-write" for c in calls)


def test_invalid_or_secret_bearing_broker_policy_never_reflected_in_gtk(monkeypatch):
    w, broker, calls, modes, downloads = fixture(monkeypatch)
    broker.inventory_refresh_schedule = lambda sid: {
        "mode": "https://secret.invalid/?auth=PRIVATE", "interval_minutes": True,
    }
    w._refresh_sync_page()
    button, row = controls(w)
    assert not button.sensitive
    assert "PRIVATE" not in row.subtitle
    broker.inventory_refresh_schedule = lambda sid: {
        "mode": "manual", "interval_minutes": 60,
    }
    broker.set_inventory_refresh_schedule = lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("https://private.invalid/?bearer=SECRET"))
    w._refresh_sync_page()
    controls(w)[0].click()
    Dialog.shown[-1].choose("periodic")
    assert ("error", "Planification des notices Kobo indisponible.",
            "Synchronisation") in calls
    assert "SECRET" not in repr(calls)
    assert downloads == {}


def test_page_no_optional_broker_adds_only_inert_row(monkeypatch):
    w, broker, calls, _build, _page = harness._fixture()
    button, row = controls(w)
    assert not button.sensitive
    assert "indisponible" in row.subtitle
    assert not w.runner.tasks


def test_non_kobo_source_never_has_inventory_schedule_control(monkeypatch):
    src = harness._source(
        provider_id="paris", sync_capabilities=("remote_inventory",),
    )
    w, broker, calls, modes, downloads = fixture(monkeypatch, sources=[src])
    assert len(w._sync_sources_box.children[0].children) == 1
    assert modes == downloads == {}
