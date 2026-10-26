"""#3101: consumer provenance is captured at Core/UI invocation, never guessed.

All upstreams are synthetic. No BnF-Prêt, network, ADEPT or sensitive user
configuration is inspected by diagnostics.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.gui import startup_coordinator
from cifereca.services import LibraryService
from tests.test_core_p0_1143_provider_connect_privacy import _rig


def _events(service):
    return [
        item["detail"]
        for item in service.diagnostics.snapshot()
        if item["operation"] == "provider_connect"
    ]


@pytest.mark.parametrize("category", (
    "startup/connect_configured", "explicit_refresh",
    "profile_change", "other",
))
def test_facade_passes_only_selected_safe_consumer_for_all_one_request_phases(category):
    service, session, plugin = _rig(succeeds=True)
    LibraryService.connect_configured_with_caller(service, caller_category=category)
    events = _events(service)
    assert [row["phase"] for row in events] == [
        "requested", "connect_invoked", "terminal",
    ]
    assert {row["caller_category"] for row in events} == {category}
    assert len({row["operation_ref"] for row in events}) == 1
    assert session.connect.call_count == 1
    assert {row["network_auth"] for row in events} == {"unknown"}


def test_caller_default_does_not_claim_unattested_startup():
    service, session, plugin = _rig(succeeds=True)
    LibraryService.connect_configured(service)
    assert {x["caller_category"] for x in _events(service)} == {"other"}


@pytest.mark.parametrize("bad_category", (
    "startup/PRIVATE_ACCOUNT", "http://private.invalid/token=PRIVATE",
    "", "plugin_activation;PRIVATE",
))
def test_unverified_caller_prose_is_not_published(bad_category):
    service, session, plugin = _rig(succeeds=True)
    provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category=bad_category,
    )
    assert {row["caller_category"] for row in _events(service)} == {"other"}
    assert "PRIVATE" not in repr(_events(service))
    assert "private.invalid" not in repr(_events(service))


@pytest.mark.parametrize("startup_seen,expected", (
    (False, "startup/connect_configured"),
    (True, "explicit_refresh"),
))
def test_gtk_submission_freezes_real_caller_before_running_worker(
    startup_seen, expected,
):
    submitted = []
    requested = []
    def connect(*, caller_category):
        requested.append(caller_category)
        return {"accounts": [], "connected": [], "errors": {}}
    window = NS(
        _closed=False,
        _source_inventory_startup_connection_seen=startup_seen,
        service=NS(connect_configured=lambda: None,
                   connect_configured_with_caller=connect),
        runner=NS(submit=lambda *a, **kw: submitted.append((a, kw))),
    )
    assert startup_coordinator.connect_configured_sources(
        window, idle_add=lambda fn: None,
    ) is False
    assert len(submitted) == 1
    work = submitted[0][0][1]
    assert requested == []  # no eager network on GUI thread
    assert work.func == connect
    assert work.keywords == {"caller_category": expected}
    assert work()["connected"] == []
    assert requested == [expected]


def test_caller_is_snapshot_at_submission_not_worker_state():
    scheduled = []
    window = NS(
        _closed=False,
        _source_inventory_startup_connection_seen=False,
        service=NS(
            connect_configured=lambda: "legacy",
            connect_configured_with_caller=lambda *, caller_category: caller_category,
        ),
        runner=NS(submit=lambda *a, **kw: scheduled.append(a[1])),
    )
    startup_coordinator.connect_configured_sources(window, idle_add=lambda fn: None)
    window._source_inventory_startup_connection_seen = True
    assert scheduled[0]() == "startup/connect_configured"
    startup_coordinator.connect_configured_sources(window, idle_add=lambda fn: None)
    assert scheduled[1]() == "explicit_refresh"

@pytest.mark.parametrize("hint,expected", (
    ("plugin_activation", "plugin_activation"),
    ("PRIVATE_ACCOUNT", "explicit_refresh"),
    (None, "explicit_refresh"),
))
def test_only_known_reload_marker_changes_the_gui_caller(hint, expected):
    submitted = []
    window = NS(
        _closed=False,
        _source_inventory_startup_connection_seen=True,
        _provider_connect_caller_hint=hint,
        service=NS(
            connect_configured=lambda: None,
            connect_configured_with_caller=lambda *, caller_category: caller_category,
        ),
        runner=NS(submit=lambda *args, **kwargs: submitted.append(args[1])),
    )
    startup_coordinator.connect_configured_sources(window, idle_add=lambda fn: None)
    assert len(submitted) == 1
    assert submitted[0]() == expected
    assert "PRIVATE_ACCOUNT" not in repr(submitted[0].keywords)


def test_plugin_reload_marks_only_its_own_connection_submission():
    from cifereca.gui.plugin_reload_controller import plugins_changed
    from tests.test_core_ui_1130_plugin_reload_controller import _window

    window, calls, _initial, _updated = _window()
    submitted = []
    window._source_inventory_startup_connection_seen = True
    window.service.connect_configured = lambda: None
    window.service.connect_configured_with_caller = (
        lambda *, caller_category: caller_category
    )
    window.runner = NS(submit=lambda *args, **kwargs: submitted.append(args[1]))
    window._connect_sources_if_open = lambda: startup_coordinator.connect_configured_sources(
        window, idle_add=lambda fn: None,
    )

    plugins_changed(window)
    assert [call() for call in submitted] == ["plugin_activation"]
    assert getattr(window, "_provider_connect_caller_hint", None) is None

    window._connect_sources_if_open()
    assert [call() for call in submitted] == [
        "plugin_activation", "explicit_refresh",
    ]


def test_plugin_reload_restores_prior_marker_even_when_reconnect_raises():
    from cifereca.gui.plugin_reload_controller import plugins_changed
    from tests.test_core_ui_1130_plugin_reload_controller import _window

    window, _calls, _initial, _updated = _window()
    window._source_inventory_startup_connection_seen = True
    window._provider_connect_caller_hint = "profile_change"

    def failed_submission():
        assert window._provider_connect_caller_hint == "plugin_activation"
        raise RuntimeError("synthetic local dispatch failure")

    window._connect_sources_if_open = failed_submission
    with pytest.raises(RuntimeError, match="synthetic local dispatch failure"):
        plugins_changed(window)
    assert window._provider_connect_caller_hint == "profile_change"
