"""#981: same-scope passive Library navigation is not an invalidation."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.library_refresh import passive_library_refresh, invalidate_library
from cifereca.gui.route_display_controller import show_route
from tests.test_core_ui_identity_async_recent import _harness, _change_identity
from tests.test_core_ui_1130_route_display_controller import _context, GTK


def test_route_passive_requests_join_current_read_and_render_once():
    host = _harness()
    route, events = _context(library=[])
    for name, value in vars(route).items():
        if not hasattr(host, name):
            setattr(host, name, value)
    host.service.library_entries = Mock(return_value=[])
    # The initial empty cache triggers one admission. Re-entering the route
    # before either the worker or GTK callback runs is only an observer.
    show_route(host, "library", Gtk=GTK)
    task = host.runner.requests[-1]
    for _ in range(3):
        show_route(host, "library", Gtk=GTK)
    result = task.work()
    for _ in range(3):
        show_route(host, "library", Gtk=GTK)
    assert len(host.runner.requests) == 1
    assert not getattr(host, "_library_refresh_dirty", False)
    task.ok(result)
    task.ok(result)
    show_route(host, "library", Gtk=GTK)
    assert len(host.runner.requests) == 1
    assert host.library_shown == [[]]
    host.service.library_entries.assert_called_once_with(identity_id="alice", scan=False)


@pytest.mark.parametrize("change", ["identity", "aba", "epoch", "mode"])
def test_passive_navigation_never_adopts_old_scope(change):
    host = _harness()
    host.service.library_entries = Mock(return_value=[{"identity_id": "alice"}])
    host.service.library_entries_all_profiles = Mock(return_value=[])
    host.refresh_library()
    old = host.runner.requests[-1]
    old_result = old.work()
    if change == "identity":
        _change_identity(host, "bob")
    elif change == "aba":
        _change_identity(host, "bob")
        _change_identity(host, "alice")
    elif change == "epoch":
        host.service._source_context_epoch = 1
    else:
        host._library_all_profiles = True
    passive_library_refresh(host)
    assert len(host.runner.requests) == 1
    assert host._library_refresh_dirty
    old.ok(old_result)
    assert host.library_shown == []
    assert len(host.runner.requests) == 2
    task = host.runner.requests[-1]
    passive_library_refresh(host)
    task.ok(task.work())
    assert len(host.runner.requests) == 2
    assert len(host.library_shown) == 1
    if change == "mode":
        host.service.library_entries_all_profiles.assert_called_once_with()
    else:
        assert host.service.library_entries.call_count == 2
        assert host.service.library_entries.call_args.kwargs["identity_id"] == (
            "bob" if change == "identity" else "alice"
        )


def test_rebound_service_gets_own_read_without_waiting_for_old_callback():
    host = _harness()
    host.refresh_library()
    old = host.runner.requests[-1]
    host.service = NS(current_identity=NS(identity_id="alice"),
                      library_entries=Mock(return_value=[{"identity_id": "alice", "new": True}]))
    passive_library_refresh(host)
    assert len(host.runner.requests) == 2
    new = host.runner.requests[-1]
    old.ok([])
    assert host._library_refresh_pending
    new.ok(new.work())
    assert host.library_shown == [[{"identity_id": "alice", "new": True}]]


@pytest.mark.parametrize("method", ["notification", "explicit"])
def test_passive_observation_does_not_swallow_real_refresh(method):
    host = _harness()
    host.refresh_library()
    old = host.runner.requests[-1]
    passive_library_refresh(host)
    if method == "notification":
        invalidate_library(host)
        old.ok(old.work())
        assert host.library_shown == []
    else:
        host.refresh_library(False, control=object())
        old.ok(old.work())
        assert host.library_shown == []
    assert len(host.runner.requests) == 2
    fresh = host.runner.requests[-1]
    passive_library_refresh(host)
    fresh.ok(fresh.work())
    assert len(host.runner.requests) == 2 and len(host.library_shown) == 1
