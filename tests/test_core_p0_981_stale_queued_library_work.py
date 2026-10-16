"""P0 #981: queued refreshes must not issue stale profile I/O.

Use the production MainWindow adapter callbacks with a controlled TaskRunner.
No GTK runtime, remote service, timer, or actual book file is required.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from tests.test_core_ui_identity_async_recent import _harness, _change_identity


@pytest.mark.parametrize("method,service_method,shown", [
    ("refresh_library", "library_entries", "library_shown"),
    ("refresh_recent", "recent_acquisitions", "recent_shown"),
])
def test_queued_old_identity_is_skipped_and_current_retried_once(
    method, service_method, shown,
):
    host = _harness()
    source = Mock(side_effect=lambda **opts: [{"identity_id": opts["identity_id"]}])
    setattr(host.service, service_method, source)
    getattr(host, method)()
    stale = host.runner.requests[-1]

    _change_identity(host, "bob")
    assert stale.work() == []
    source.assert_not_called()
    stale.ok([])
    assert len(host.runner.requests) == 2
    assert getattr(host, shown) == []

    current = host.runner.requests[-1]
    assert current.work() == [{"identity_id": "bob"}]
    source.assert_called_once()
    assert source.call_args.kwargs["identity_id"] == "bob"
    current.ok([{"identity_id": "bob"}])
    assert getattr(host, shown) == [[{"identity_id": "bob"}]]

    # A callback delivered twice may not trigger another source read.
    stale.ok([])
    assert len(host.runner.requests) == 2
    assert source.call_count == 1


@pytest.mark.parametrize("method,service_method", [
    ("refresh_library", "library_entries"),
    ("refresh_recent", "recent_acquisitions"),
])
def test_a_to_b_to_a_queued_work_does_not_reuse_wrong_generation(
    method, service_method,
):
    host = _harness()
    source = Mock(return_value=[])
    setattr(host.service, service_method, source)
    getattr(host, method)()
    original = host.runner.requests[-1]
    _change_identity(host, "bob")
    _change_identity(host, "alice")
    assert original.work() == []  # same ID, but a new identity generation
    source.assert_not_called()
    original.ok([])
    assert len(host.runner.requests) == 2
    active = host.runner.requests[-1]
    active.work()
    source.assert_called_once()
    assert source.call_args.kwargs["identity_id"] == "alice"


@pytest.mark.parametrize("method,service_method", [
    ("refresh_library", "library_entries"),
    ("refresh_recent", "recent_acquisitions"),
])
def test_newer_user_refresh_supersedes_background_worker_without_service_io(
    method, service_method,
):
    host = _harness()
    source = Mock(return_value=[{"current": True}])
    setattr(host.service, service_method, source)
    getattr(host, method)()
    old = host.runner.requests[-1]
    getattr(host, method)(control=object())
    new = host.runner.requests[-1]
    assert len(host.runner.requests) == 2
    assert old.work() == []
    source.assert_not_called()
    # The superseded callback must not clear the new pending flag.
    old.ok([])
    assert host._library_refresh_pending if method == "refresh_library" else host._recent_refresh_pending
    assert new.work() == [{"current": True}]
    assert source.call_count == 1
    new.ok([{"current": True}])
    assert len(host.runner.requests) == 2


@pytest.mark.parametrize("mode", ["scan", "covers"])
def test_stale_library_scan_must_not_touch_disk_or_cover_provider(mode):
    host = _harness()
    source = Mock(return_value=[])
    disk_scan = Mock()
    host.service.library_entries = source
    host.service.catalog_scan = disk_scan
    host.refresh_library(True, control=object(), refresh_covers=(mode == "covers"))
    old = host.runner.requests[-1]
    _change_identity(host, "bob")
    assert old.work() == []
    source.assert_not_called()
    disk_scan.assert_not_called()
    old.ok([])
    next_task = host.runner.requests[-1]
    assert next_task.kwargs["control"] is None
    # The stale manual scan is not replayed as an automatic scan.
    next_task.work()
    source.assert_called_once_with(identity_id="bob", scan=False)
    disk_scan.assert_not_called()


@pytest.mark.parametrize("method,service_method", [
    ("refresh_library", "library_entries"),
    ("refresh_recent", "recent_acquisitions"),
])
def test_disposed_owner_does_not_execute_already_queued_source_io(
    method, service_method,
):
    host = _harness()
    source = Mock()
    setattr(host.service, service_method, source)
    getattr(host, method)()
    request = host.runner.requests[-1]
    host._closed = True
    assert request.work() == []
    request.ok([])
    request.err(RuntimeError("PRIVATE_STALE_ERROR"))
    source.assert_not_called()
    assert len(host.runner.requests) == 1


def test_current_manual_scan_keeps_existing_explicit_disk_policy():
    host = _harness()
    host.service.catalog_scan = Mock()
    host.service.library_entries = Mock(return_value=[])
    host.refresh_library(True, control=object(), refresh_covers=True)
    request = host.runner.requests[-1]
    assert request.work() == []
    host.service.catalog_scan.assert_called_once_with(force_covers=True)
    host.service.library_entries.assert_called_once_with(
        identity_id="alice", scan=False,
    )


@pytest.mark.parametrize("method,shown,marker", [
    ("refresh_library", "library_shown", "_library_loaded_generation"),
    ("refresh_recent", "recent_shown", "_recent_loaded_generation"),
])
def test_successful_empty_response_records_current_generation_only(
    method, shown, marker,
):
    host = _harness()
    getattr(host, method)()
    old = host.runner.requests[-1]
    _change_identity(host, "bob")
    assert old.work() == []
    old.ok([])
    assert getattr(host, marker, None) is None
    fresh = host.runner.requests[-1]
    assert fresh.work() == [{"identity_id": "bob"}]
    fresh.ok([])
    assert getattr(host, marker) == host._identity_context_generation
    assert getattr(host, shown) == [[]]
    _change_identity(host, "alice")
    assert getattr(host, marker) != host._identity_context_generation


@pytest.mark.parametrize("method,marker", [
    ("refresh_library", "_library_loaded_generation"),
    ("refresh_recent", "_recent_loaded_generation"),
])
def test_failed_refresh_never_marks_an_empty_snapshot_successful(method, marker):
    host = _harness()
    host.diagnostics.add = Mock()
    getattr(host, method)()
    request = host.runner.requests[-1]
    request.err(RuntimeError("PRIVATE_FAILURE"))
    assert getattr(host, marker, None) is None
    host.diagnostics.add.assert_called_once()
    assert len(host.runner.requests) == 1
