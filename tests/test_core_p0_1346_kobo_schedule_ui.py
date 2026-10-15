"""P0 #1346/#1347 — Kobo auto-acquisition schedule is human opt-in per source.

Use the actual GTK controller function bodies with headless widget doubles.
No Kobo network, ADEPT, disk download or global opening sync occurs.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
import pytest

from tests import test_core_ui_1347_sync_action_scope as harness
from tests.test_core_p0_1347_kobo_confirmed_per_account import Dialog


def fixture(monkeypatch, *, sources=None, schedule=None):
    Dialog.shown.clear()
    methods = harness._methods()
    env = methods["_refresh_sync_page"].__globals__
    env["Adw"].MessageDialog = Dialog
    env["Adw"].ResponseAppearance = NS(SUGGESTED=1)
    monkeypatch.setattr(harness, "_methods", lambda: methods)
    window, broker, calls, _build, _page = harness._fixture(sources=sources)
    modes = dict(schedule or {})
    reader = lambda sid: (calls.append(("schedule-read", sid)) or modes.get(sid, "manual"))

    def writer(sid, mode):
        calls.append(("schedule-write", sid, mode))
        if mode not in {"manual", "on_start"}:
            raise ValueError("unsupported schedule")
        modes[sid] = mode
        return mode

    broker.local_materialization_schedule = reader
    broker.set_local_materialization_schedule = writer
    window._refresh_sync_page()
    return window, broker, calls, modes


def controls(window, idx=0):
    group = window._sync_sources_box.children[idx]
    # Preserve the narrow GTK source/acquisition rows for 1366px displays.
    assert len(group.children[0].suffixes) == 4
    assert len(group.children[3].suffixes) == 2
    row = group.children[4]
    assert len(row.suffixes) == 1
    return row.suffixes[0], row


def test_manual_is_default_and_page_refresh_cannot_enable_or_download(monkeypatch):
    w, _broker, calls, modes = fixture(monkeypatch)
    btn, row = controls(w)
    assert btn.sensitive
    assert btn.label == "Autoriser au démarrage…"
    assert "Mode manuel" in row.subtitle
    assert "aucun téléchargement" in row.subtitle
    w._refresh_sync_page()
    assert modes == {}
    assert not any(c[0] == "schedule-write" for c in calls)
    assert not any(c[0] in {"prepare", "acquire", "sync-source"} for c in calls)
    assert not w.runner.tasks


def test_opt_in_requires_separate_dialog_and_scope_valid_at_confirmation(monkeypatch):
    w, _broker, calls, modes = fixture(monkeypatch)
    btn, _row = controls(w)
    btn.click()
    assert len(Dialog.shown) == 1
    assert Dialog.shown[0].presented
    assert "compte Kobo" in Dialog.shown[0].body
    assert "sans nouvelle confirmation" in Dialog.shown[0].body
    assert "Aucun fichier n’est téléchargé maintenant" in Dialog.shown[0].body
    assert modes == {}
    assert not w.runner.tasks
    Dialog.shown[-1].choose("cancel")
    assert modes == {}
    assert not any(c[0] == "schedule-write" for c in calls)


def test_confirmation_enables_only_target_account_not_global(monkeypatch):
    sources = [
        harness._source(source_id="kobo-one", label="Deux Kobo"),
        harness._source(source_id="kobo-two", label="Deux Kobo"),
    ]
    w, _broker, calls, modes = fixture(monkeypatch, sources=sources)
    btn, row = controls(w, 0)
    btn.click()
    assert len(Dialog.shown) == 1
    Dialog.shown[-1].choose("confirm")
    assert modes == {"kobo-one": "on_start"}
    assert ("schedule-write", "kobo-one", "on_start") in calls
    assert not any(c[0] == "schedule-write" and c[1] == "kobo-two" for c in calls)
    assert not w.runner.tasks
    current, row = controls(w, 0)
    assert current.label == "Désactiver au démarrage"
    assert "ouverture" in row.subtitle
    other, row = controls(w, 1)
    assert other.label == "Autoriser au démarrage…"


def test_disabling_is_immediate_and_possible_when_source_disconnected(monkeypatch):
    sources = [harness._source(connected=False, enabled=False)]
    w, _broker, calls, modes = fixture(
        monkeypatch, sources=sources, schedule={"kobo-one": "on_start"},
    )
    btn, row = controls(w)
    assert btn.sensitive
    assert btn.label == "Désactiver au démarrage"
    btn.click()
    assert modes["kobo-one"] == "manual"
    assert ("schedule-write", "kobo-one", "manual") in calls
    assert Dialog.shown == []
    assert not w.runner.tasks


@pytest.mark.parametrize("connected,enabled,capability", [
    (False, True, True),
    (True, False, True),
    (True, True, False),
])
def test_noneligible_sources_cannot_gain_consent(monkeypatch, connected, enabled, capability):
    sources = [harness._source(
        connected=connected, enabled=enabled,
        sync_capabilities=("local_materialization",) if capability else (),
    )]
    w, _broker, calls, modes = fixture(monkeypatch, sources=sources)
    btn, _row = controls(w)
    assert not btn.sensitive
    btn.click()
    assert modes == {} and not Dialog.shown
    assert not any(c[0] == "schedule-write" for c in calls)


def test_dialog_is_revoked_after_identity_a_b_a_or_page_refresh(monkeypatch):
    w, _broker, calls, modes = fixture(monkeypatch)
    w._identity_context_generation = 2
    w._refresh_sync_page()
    btn, _row = controls(w)
    btn.click()
    w._identity_context_generation = 4
    Dialog.shown[-1].choose("confirm")
    assert modes == {}
    assert not any(c[0] == "schedule-write" for c in calls)

    w._refresh_sync_page()
    btn, _row = controls(w)
    btn.click()
    w._refresh_sync_page()
    Dialog.shown[-1].choose("confirm")
    assert modes == {}


def test_stale_dialog_does_not_overwrite_another_update(monkeypatch):
    w, _broker, calls, modes = fixture(monkeypatch)
    btn, _row = controls(w)
    btn.click()
    modes["kobo-one"] = "on_start"
    Dialog.shown[-1].choose("confirm")
    assert modes == {"kobo-one": "on_start"}
    assert not any(c[0] == "schedule-write" for c in calls)


def test_provider_error_and_malformed_mode_never_expose_secret(monkeypatch):
    w, broker, calls, modes = fixture(monkeypatch)
    def failed_writer(*args):
        raise ValueError("https://PRIVATE/?token=SECRET")
    broker.set_local_materialization_schedule = failed_writer
    # The callback's bound writer is captured at render time.
    w._refresh_sync_page()
    controls(w)[0].click()
    Dialog.shown[-1].choose("confirm")
    assert any(c[0] == "error" for c in calls)
    assert "SECRET" not in repr(calls)
    assert modes == {}

    broker.local_materialization_schedule = lambda sid: "token=SECRET"
    w._refresh_sync_page()
    btn, row = controls(w)
    assert not btn.sensitive
    assert "SECRET" not in row.subtitle


def test_non_kobo_source_gets_no_schedule_control(monkeypatch):
    providers = [harness._source(provider_id="paris", sync_capabilities=("remote_inventory",))]
    w, _broker, calls, modes = fixture(monkeypatch, sources=providers)
    assert len(w._sync_sources_box.children[0].children) == 1
    assert not any(c[0] == "schedule-write" for c in calls)
    assert modes == {}
