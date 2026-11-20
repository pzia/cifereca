"""#981: loaded snapshots are scoped to their admitted volatile context."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.library_refresh import passive_library_refresh
from cifereca.gui.route_display_controller import show_route
from tests.test_core_ui_identity_async_recent import _harness
from tests.test_core_ui_1130_route_display_controller import _context, GTK


def _window(initial):
    host = _harness()
    route, events = _context(library=[])
    for name, value in vars(route).items():
        if not hasattr(host, name):
            setattr(host, name, value)
    host._library_scope_generation = 0
    host.service.library_entries = Mock(return_value=initial)
    def display(entries):
        host._library_entries_cache = list(entries)
        host.library_shown.append(entries)
    host._show_library_entries = display
    return host, events


@pytest.mark.parametrize("initial", [[], [{"identity_id": "alice", "old": True}]])
@pytest.mark.parametrize("change", ["epoch", "core", "view_aba"])
def test_cached_library_revisited_only_under_exact_loaded_scope(initial, change):
    host, events = _window(initial)
    show_route(host, "library", Gtk=GTK)
    first, = host.runner.requests
    first.ok(first.work())
    assert host._library_entries_cache == initial
    assert host.library_shown == [initial]
    # An unchanged context uses the loaded snapshot, even if it was empty.
    show_route(host, "library", Gtk=GTK)
    assert len(host.runner.requests) == 1
    assert events[-1] == ("library", initial)

    replacement = [{"identity_id": "alice", "new": True}]
    if change == "epoch":
        host.service._source_context_epoch = 1
    elif change == "core":
        host.service = NS(current_identity=host.service.current_identity)
    else:
        # Current -> all -> current; same mode, different view generation.
        host._library_scope_generation += 2
    host.service.library_entries = Mock(return_value=replacement)
    events.clear()
    for _ in range(3):
        show_route(host, "library", Gtk=GTK)
    assert host._library_entries_cache == []
    assert [e for e in events if e[0] == "library"] == [("library", [])] * 3
    assert len(host.runner.requests) == 2
    second = host.runner.requests[-1]
    second.ok(second.work())
    second.ok(replacement)
    assert host.library_shown == [initial, replacement]
    host.service.library_entries.assert_called_once_with(identity_id="alice", scan=False)
    show_route(host, "library", Gtk=GTK)
    assert len(host.runner.requests) == 2
    assert events[-1] == ("library", replacement)


def test_pending_same_mode_aba_is_a_distinct_library_read():
    host, _ = _window([{"identity_id": "alice", "old": True}])
    host.refresh_library()
    first, = host.runner.requests
    previous = first.work()
    host._library_scope_generation += 2
    passive_library_refresh(host)
    assert host._library_refresh_dirty
    first.ok(previous)
    assert not host.library_shown
    assert len(host.runner.requests) == 2
    fresh = [{"identity_id": "alice", "new": True}]
    host.service.library_entries.return_value = fresh
    second = host.runner.requests[-1]
    second.ok(second.work())
    assert host.library_shown == [fresh]
    assert host.service.library_entries.call_count == 2
