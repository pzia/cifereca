"""#4673: the Search GUI must never merge two Paris account snapshots.

Exercise the actual GTK search_controller.run_search body through its
MainWindow-backed headless host; no provider I/O or mutations.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from tests.test_search_controller_state import _host


def _book(identity):
    return NS(
        id="edition-" + identity,
        source_provider="paris",
        source_credential="card-" + identity,
    )


def _deliver(host, row):
    request = host.requests[-1]
    request.work()
    on_batch = host.online.call_args.args[2]
    on_batch(NS(results=(row,), provider_id="paris", result_status="live", error_code=""))
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    host.service.last_search = [row]
    request.done(NS(warnings=[]))


def test_same_query_new_online_request_retires_old_paris_detail_and_rows():
    host = _host()
    host.run_search(True)
    first = _book("alice")
    _deliver(host, first)
    assert host._remote_search_results == [first]

    # Same visible query, profile and provider, but a different Paris
    # primary was selected in Settings since the previous completed search.
    host._populate_search.reset_mock()
    host.run_search(True)
    assert host._remote_search_results == []
    assert host._loaded_search_context is None
    host._populate_search.assert_called_once_with([], announce=False)

    second = _book("alice-secondary")
    _deliver(host, second)
    assert host._remote_search_results == [second]
    assert all(row.source_credential != first.source_credential
               for row in host._remote_search_results)


def test_old_batch_cannot_repopulate_new_same_query_request():
    host = _host()
    host.run_search(True)
    old = host.requests[-1]
    old.work()
    old_callback = host.online.call_args.args[2]
    old_callback(NS(results=(_book("old"),)))
    old_delivery = host.scheduled.pop()

    # While online work is pending, a same-query second click is correctly
    # coalesced. Switching to known/local search first revokes that request;
    # an explicit new online click then opens a fresh source generation.
    host.run_search(False)
    host.run_search(True)
    assert old_delivery[0](*old_delivery[1]) is False
    assert host._remote_search_results == []
    old.done(NS(warnings=["old"]))
    assert host._remote_search_results == []

    fresh = _book("new")
    _deliver(host, fresh)
    assert host._remote_search_results == [fresh]


def test_local_facet_rerender_preserves_current_request_snapshot():
    host = _host()
    host.run_search(True)
    current = _book("alice")
    _deliver(host, current)

    host._render_search_results = Mock()
    before = len(host.requests)
    host._search_controls_changed()
    host._render_search_results.assert_called_once_with()
    assert host._remote_search_results == [current]
    assert len(host.requests) == before
