"""P1 #4277: record the exact unavailable-dialog presentation, not a guess.

No provider login, network, account, GTK runtime or authentication is needed.
Only fixed closed diagnostic facts are emitted. Obsolete titlebar actions
remain inert and must not be described as the proven popup cause.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.feedback_dialog_controller import show_error
from cifereca.gui.plugin_action_dispatch_controller import invoke_ui_action
from cifereca.gui.ui_dialog_observation import (
    action_dialog_context, observe_unavailable_dialog,
)
from cifereca.plugin_api import UiActionContribution, UiPageContribution
from tests.test_core_ui_1130_header_action_controller import (
    _window as header_window, _refresh as refresh_header,
)


SECRET = "PRIVATE_ACCESS_TOKEN_4277"
SENTINEL_URL = "https://private.invalid/?token=" + SECRET


class FakeDialog:
    def __init__(self, title, body, captured):
        self.title, self.body, self.captured = title, body, captured

    def add_response(self, *_args):
        pass

    def set_default_response(self, *_args):
        pass

    def set_close_response(self, *_args):
        pass

    def present(self):
        self.captured.append(("present", self.title, self.body))


def rig(*, selected=True, route="search", action_callback=None):
    captured, diagnostics = [], []
    def make_dialog(window, title, body):
        return FakeDialog(title, body, captured)
    adw = NS(MessageDialog=NS(new=make_dialog))
    # An actual declared header action, not the generated implicit open.
    action = UiActionContribution(
        "refresh", "Refresh", action_callback or (lambda _host: None),
        "view-refresh-symbolic",
    )
    page = UiPageContribution(
        route="search", title="Recherche",
        factory=lambda _host: None, actions=(action,),
    )
    window = NS(
        _closed=False,
        _pages={"search": page},
        view_stack=NS(get_visible_child_name=lambda: route),
        service=NS(current_identity=NS(identity_id=SECRET) if selected else None),
        diagnostics=NS(add=lambda *args, **kwargs: diagnostics.append((args, kwargs))),
        _functional_error_message=lambda message: message,
        toast=lambda text, timeout: captured.append(("toast", text, timeout)),
        shell_menu=NS(popdown=lambda: captured.append(("popdown",))),
    )
    window.show_error = lambda message, title="Une erreur est survenue": show_error(
        window, message, title, Adw=adw,
    )
    return window, action, diagnostics, captured, adw


def evidence(events):
    return [entry[1]["detail"] for entry in events
            if entry[0] == ("UI", "action unavailable dialog", "warning")]


def test_real_modal_from_exact_registered_ui_action_has_proven_click_context():
    def fail(_host):
        raise RuntimeError("provider said " + SENTINEL_URL)
    window, action, diagnostics, captured, _adw = rig(action_callback=fail)
    invoke_ui_action(window, action, NS())
    assert captured[0] == ("popdown",)
    assert ("present", "Action impossible", "Traitement indisponible") in captured
    assert evidence(diagnostics) == [{
        "schema_version": 1,
        "code": "action_unavailable",
        "trigger": "user_click",
        "action_id": "search.refresh",
        "route": "search",
        "ui_scope": "selected_identity",
        "provider_id": "unknown",
        "task_ref": None,
    }]
    assert not hasattr(window, "_ui_dialog_observation_context")
    assert SECRET not in repr(diagnostics)
    assert SENTINEL_URL not in repr(captured)


@pytest.mark.parametrize(("title", "message", "expected_count"), [
    ("Action non disponible", "Details unknown", 1),
    ("Action impossible", "Something", 1),
    ("Une erreur est survenue", "Traitement indisponible", 1),
    ("Une erreur est survenue", "Action non disponible", 1),
    ("Synchronisation impossible", "Échec de connexion", 0),
    ("Une erreur est survenue", "Other request failed", 0),
])
def test_closed_taxonomy_only_on_action_unavailability(title, message, expected_count):
    window, _action, diagnostics, captured, _ = rig(selected=False)
    window.show_error(message, title)
    assert len(evidence(diagnostics)) == expected_count
    assert captured[-1] == ("present", title, message)
    if expected_count:
        assert evidence(diagnostics)[0]["trigger"] == "unknown"
        assert evidence(diagnostics)[0]["ui_scope"] == "no_identity"


def test_diagnostic_writes_happen_only_after_successful_dialog_present():
    window, _action, diagnostics, captured, adw = rig()
    class BrokenDialog(FakeDialog):
        def present(self):
            raise RuntimeError("widget destroyed")
    adw.MessageDialog.new = lambda window, title, body: BrokenDialog(
        title, body, captured,
    )
    with pytest.raises(RuntimeError, match="widget destroyed"):
        window.show_error("Traitement indisponible")
    assert diagnostics == []


@pytest.mark.parametrize("closed", [True, False])
def test_diagnostic_failure_never_prevents_visible_or_closed_window(closed):
    window, _action, diagnostics, captured, adw = rig()
    window._closed = closed
    window.diagnostics.add = Mock(side_effect=RuntimeError("observer unavailable"))
    window.show_error("Traitement indisponible")
    assert window.diagnostics.add.call_count == (0 if closed else 1)
    assert len([c for c in captured if c[0] == "present"]) == (0 if closed else 1)


def test_unregistered_or_invalid_action_id_cannot_be_emitted_as_canonical():
    window, action, diagnostics, captured, _ = rig(route=SENTINEL_URL)
    unregistered = UiActionContribution(
        "PRIVATE_ACCESS_TOKEN_4277", "Hostile", lambda _host: None, "symbolic",
    )
    with action_dialog_context(window, unregistered):
        window.show_error("Traitement indisponible")
    assert evidence(diagnostics)[0]["action_id"] == "unknown"
    assert evidence(diagnostics)[0]["route"] == "unknown"
    assert SECRET not in repr(diagnostics)


def test_diagnostic_context_is_temporary_and_reentrant_callbacks_do_not_stick():
    window, action, diagnostics, captured, _ = rig()
    window._ui_dialog_observation_context = {
        "trigger": "navigation", "action_id": "unknown",
    }
    with action_dialog_context(window, action):
        window.show_error("Traitement indisponible")
    assert window._ui_dialog_observation_context == {
        "trigger": "navigation", "action_id": "unknown",
    }
    window.show_error("Traitement indisponible")
    assert [row["trigger"] for row in evidence(diagnostics)] == [
        "user_click", "navigation",
    ]
    assert evidence(diagnostics)[1]["action_id"] == "unknown"


@pytest.mark.parametrize("candidate", [
    True, 0, -1, "22", 2**53, SENTINEL_URL,
])
def test_untrusted_or_ambiguous_task_ref_does_not_claim_correlation(candidate):
    window, action, diagnostics, captured, _ = rig()
    window._ui_dialog_observation_context = {
        "trigger": "auto_sync",
        "action_id": SENTINEL_URL,
        "task_ref": candidate,
    }
    window.show_error("Action non disponible")
    observation = evidence(diagnostics)[0]
    assert observation["trigger"] == "auto_sync"
    assert observation["task_ref"] is None
    assert observation["action_id"] == "unknown"
    assert SENTINEL_URL not in repr(diagnostics)


def test_exact_positive_task_ref_can_be_recorded_without_guessing_provider():
    window, action, diagnostics, captured, _ = rig()
    window._ui_dialog_observation_context = {
        "trigger": "async_callback", "task_ref": 42,
    }
    window.show_error("Traitement indisponible")
    observation = evidence(diagnostics)[0]
    assert observation["task_ref"] == 42
    assert observation["provider_id"] == "unknown"


def test_obsolete_bookings_titlebar_action_is_inert_on_refresh_and_connection():
    window, diagnostics = header_window(["bookings.open", "search.online"])
    refresh_header(window, "search")
    assert diagnostics[0][1]["detail"] == {
        "obsolete_action_ids": ["bookings.open"],
    }
    assert len(window.header_actions.children) == 1
    assert window.header_actions.children[0].tooltip == "Online"
    refresh_header(window, "library")
    assert diagnostics[0][1]["detail"] == {
        "obsolete_action_ids": ["bookings.open"],
    }
    assert all(not isinstance(item, tuple) or item[0] != "error"
               for item in window.events)
    # This does NOT establish that this historical preference caused a
    # real user's popup; it only rules out this deterministic callback path.


@pytest.mark.parametrize("invalidated", ["route", "header", "identity", "close"])
def test_callback_that_invalidates_its_context_then_fails_never_opens_modal(invalidated):
    state = {}
    def fail_after_transition(_host):
        window = state["window"]
        if invalidated == "route":
            window._route_display_token = object()
        elif invalidated == "header":
            window._header_action_generation = object()
        elif invalidated == "identity":
            window._identity_context_generation += 1
        else:
            window._closed = True
        raise RuntimeError("stale callback " + SENTINEL_URL)

    window, action, diagnostics, captured, _ = rig(action_callback=fail_after_transition)
    state["window"] = window
    window._route_display_token = object()
    window._header_action_generation = object()
    window._identity_context_generation = 1
    invoke_ui_action(window, action, NS())
    assert captured == [("popdown",)]
    assert diagnostics == []
    assert not hasattr(window, "_ui_dialog_observation_context")


def test_menu_dismissal_that_switches_identity_never_invokes_old_action():
    window, action, diagnostics, captured, _ = rig(
        action_callback=lambda _host: pytest.fail("stale action invoked"),
    )
    window._identity_context_generation = 3
    def change_identity():
        captured.append(("popdown",))
        window._identity_context_generation = 4
    window.shell_menu.popdown = change_identity
    invoke_ui_action(window, action, NS())
    assert captured == [("popdown",)]
    assert diagnostics == []
