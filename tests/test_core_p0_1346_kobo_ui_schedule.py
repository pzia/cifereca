"""P0 #1346: UI acquisition at startup is explicit, per-account, reversible.

GTK-controller AST is executed with headless widget and dialog doubles. No
provider, file, ADEPT or remote operation occurs during page construction.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_materialization_schedule_presentation
from tests import test_core_ui_1347_sync_action_scope as harness


class Dialog:
    shown = []

    def __init__(self, window, title, body):
        self.window, self.title, self.body = window, title, body
        self.responses = {}
        self.callbacks = {}
        self.presented = False
        self.__class__.shown.append(self)

    @classmethod
    def new(cls, window, title, body):
        return cls(window, title, body)

    def add_response(self, code, label):
        self.responses[code] = label

    def set_response_appearance(self, *args):
        pass

    def connect(self, signal, callback):
        self.callbacks[signal] = callback

    def present(self):
        self.presented = True

    def choose(self, response):
        self.callbacks["response"](self, response)


def _fixture(monkeypatch, *, sources=None, saved=None):
    Dialog.shown.clear()
    methods = harness._methods()
    env = methods["_refresh_sync_page"].__globals__
    env["Adw"].MessageDialog = Dialog
    env["Adw"].ResponseAppearance = NS(SUGGESTED=1)
    monkeypatch.setattr(harness, "_methods", lambda: methods)
    window, broker, calls, _build, _page = harness._fixture(sources=sources)
    values = dict(saved or {})

    def get_schedule(source_id):
        calls.append(("get-schedule", source_id))
        return values.get(source_id, "manual")

    def set_schedule(source_id, mode):
        calls.append(("set-schedule", source_id, mode))
        values[source_id] = mode
        return mode

    broker.local_materialization_schedule = get_schedule
    broker.set_local_materialization_schedule = set_schedule
    window._refresh_sync_page()
    return window, broker, calls, values


def _controls(window, idx=0):
    group = window._sync_sources_box.children[idx]
    # Existing source/preview/audit/manual-download rows keep stable indices.
    assert len(group.children[0].suffixes) == 4
    assert len(group.children[3].suffixes) == 2
    schedule = group.children[4]
    assert schedule.title == "Acquisition Kobo · au démarrage"
    assert len(schedule.suffixes) == 1
    return schedule.suffixes[0], schedule


@pytest.mark.parametrize("raw", ["on_start", "manual", None, "PRIVATE_TOKEN"])
def test_schedule_presentation_never_echoes_arbitrary_data(raw):
    ui = sync_materialization_schedule_presentation(
        raw, source_enabled=True, connected=True,
        capable=True, broker_supported=True,
    )
    assert "PRIVATE_TOKEN" not in repr(ui)
    assert "périodique" not in ui.action_label
    if raw == "on_start":
        assert ui.mode == "on_start" and ui.action_enabled
    elif raw == "manual":
        assert ui.mode == "manual" and ui.action_enabled
    else:
        assert ui.mode == "unavailable" and not ui.action_enabled


def test_page_defaults_to_manual_and_has_no_implicit_permission(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    button, row = _controls(window)
    assert button.label == "Activer au démarrage…"
    assert button.sensitive
    assert "Mode manuel" in row.subtitle
    assert "aucun fichier local" in row.subtitle
    assert values == {}  # read-only render
    assert not Dialog.shown
    assert not window.runner.tasks
    window._refresh_sync_page()
    assert values == {}
    assert not any(c[0] == "set-schedule" for c in calls)


def test_confirmation_is_required_for_exact_selected_kobo_account(monkeypatch):
    sources = [
        harness._source(source_id="a", label="Deux Kobo"),
        harness._source(source_id="b", label="Deux Kobo"),
    ]
    window, broker, calls, values = _fixture(monkeypatch, sources=sources)
    button, row = _controls(window, idx=1)
    button.click()
    assert not values
    assert len(Dialog.shown) == 1
    dialog = Dialog.shown[-1]
    assert dialog.presented
    assert "uniquement le compte Kobo" in dialog.body
    assert "ne télécharge rien maintenant" in dialog.body
    assert "périodique" in dialog.body
    assert "b" not in dialog.responses  # only fixed response keys
    dialog.choose("cancel")
    assert values == {}
    button.click()
    Dialog.shown[-1].choose("allow")
    assert values == {"b": "on_start"}
    assert ("set-schedule", "b", "on_start") in calls
    assert not any(x[0] == "set-schedule" and x[1] == "a" for x in calls)
    assert not window.runner.tasks
    button2, row2 = _controls(window, idx=1)
    assert "Activée" in row2.subtitle
    assert button2.label == "Désactiver au démarrage"


def test_revocation_is_one_click_even_when_source_now_disconnected(monkeypatch):
    source = harness._source(enabled=False, connected=False)
    window, broker, calls, values = _fixture(
        monkeypatch, sources=[source], saved={"kobo-one": "on_start"},
    )
    button, row = _controls(window)
    assert button.sensitive
    assert "Activée" in row.subtitle
    button.click()
    assert values["kobo-one"] == "manual"
    assert ("set-schedule", "kobo-one", "manual") in calls
    assert Dialog.shown == []


@pytest.mark.parametrize("enabled,connected,capability", [
    (False, True, True), (True, False, True), (True, True, False),
])
def test_ineligible_sources_cannot_opt_in_from_page(
    monkeypatch, enabled, connected, capability,
):
    source = harness._source(
        enabled=enabled, connected=connected,
        sync_capabilities=("local_materialization",) if capability else (),
    )
    window, broker, calls, values = _fixture(monkeypatch, sources=[source])
    button, row = _controls(window)
    assert not button.sensitive
    button.click()
    assert values == {} and Dialog.shown == []
    assert not any(x[0] == "set-schedule" for x in calls)


def test_missing_optional_broker_capability_is_non_mutating(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    broker.local_materialization_schedule = None
    broker.set_local_materialization_schedule = None
    window._refresh_sync_page()
    button, row = _controls(window)
    assert not button.sensitive
    assert "indisponible" in row.subtitle
    button.click()
    assert not any(x[0] == "set-schedule" for x in calls)


def test_stale_dialog_after_identity_change_cannot_enable(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    button, row = _controls(window)
    button.click()
    window.service.current_identity = NS(identity_id="bob")
    Dialog.shown[-1].choose("allow")
    assert values == {} and not any(x[0] == "set-schedule" for x in calls)


def test_aba_identity_generation_change_cannot_enable(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    window._identity_context_generation = 8
    window._refresh_sync_page()
    button, row = _controls(window)
    button.click()
    window._identity_context_generation = 10
    Dialog.shown[-1].choose("allow")
    assert values == {} and not any(x[0] == "set-schedule" for x in calls)


def test_old_button_or_dialog_from_rebuilt_page_cannot_enable(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    button, row = _controls(window)
    button.click()
    window._refresh_sync_page()
    button.click()
    Dialog.shown[-1].choose("allow")
    assert values == {} and not any(x[0] == "set-schedule" for x in calls)


def test_closed_window_cannot_accept_prior_auto_permission(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    button, row = _controls(window)
    button.click()
    window._closed = True
    Dialog.shown[-1].choose("allow")
    assert values == {} and not any(x[0] == "set-schedule" for x in calls)


def test_provider_error_is_sanitized_without_mutating_policy(monkeypatch):
    window, broker, calls, values = _fixture(monkeypatch)
    broker.set_local_materialization_schedule = lambda *_args: (
        (_ for _ in ()).throw(RuntimeError(
            "https://entrepot2r.numilog.com/?auth=PRIVATE_TOKEN"
        ))
    )
    window._refresh_sync_page()
    button, row = _controls(window)
    button.click()
    Dialog.shown[-1].choose("allow")
    assert not any(x[0] == "set-schedule" for x in calls)
    assert calls[-1] == (
        "error", "Planification Kobo indisponible pour ce compte.",
        "Synchronisation",
    )
    assert "PRIVATE_TOKEN" not in str(calls)


def test_wrong_provider_never_offers_materialization_schedule(monkeypatch):
    sources = [harness._source(
        provider_id="numilog", source_id="store",
        sync_capabilities=("remote_inventory",),
    )]
    window, broker, calls, values = _fixture(monkeypatch, sources=sources)
    group = window._sync_sources_box.children[0]
    assert len(group.children) == 1
    assert not any(c[0] == "get-schedule" for c in calls)
    assert values == {}
