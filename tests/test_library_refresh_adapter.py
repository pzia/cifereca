"""Exercise the real headless adapter through MainWindow's public facades."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui import library_refresh
from cifereca.task_queue import TaskPriority
from tests.test_core_ui_identity_async_recent import _harness


@pytest.mark.parametrize("scan,covers", [(False, False), (True, False), (True, True)])
def test_library_work_keeps_explicit_cover_scan_policy_and_identity(scan, covers):
    host = _harness()
    host.service.catalog_scan = Mock()
    host.service.library_entries = Mock(return_value=[{"identity_id": "alice"}])
    control = object()
    host.refresh_library(scan, control, refresh_covers=covers)
    request = host.runner.requests[-1]
    # Submitting the adapter does not run service IO on the caller's thread.
    host.service.catalog_scan.assert_not_called()
    host.service.library_entries.assert_not_called()
    assert request.kwargs == {
        "control": control, "priority": TaskPriority.USER_ACTION, "kind": "library",
    }
    assert request.work() == [{"identity_id": "alice"}]
    host.service.library_entries.assert_called_once_with(
        identity_id="alice", scan=scan and not covers,
    )
    if covers:
        host.service.catalog_scan.assert_called_once_with(force_covers=True)
    else:
        host.service.catalog_scan.assert_not_called()


@pytest.mark.parametrize("name,kind", [("refresh_library", "library"), ("refresh_recent", "recent")])
def test_background_refresh_keeps_normal_priority_and_no_control(name, kind):
    host = _harness()
    getattr(host, name)()
    assert host.runner.requests[-1].kwargs == {
        "control": None, "priority": TaskPriority.NORMAL, "kind": kind,
    }


def test_facades_forward_caller_arguments_without_replacing_controls(monkeypatch):
    host = _harness()
    control = object()
    library = Mock()
    recent = Mock()
    monkeypatch.setattr(library_refresh, "refresh_library", library)
    monkeypatch.setattr(library_refresh, "refresh_recent", recent)
    host.refresh_library(scan=True, control=control, refresh_covers=True)
    host.refresh_recent(control=control)
    library.assert_called_once_with(host, True, control, refresh_covers=True)
    recent.assert_called_once_with(host, control)


def test_refresh_adapter_import_does_not_load_gtk_or_main_window():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('refresh adapter imports GTK')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui import library_refresh
assert 'cifereca.gui.main_window' not in sys.modules
assert 'gi' not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code], env=os.environ.copy(),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr

@pytest.mark.parametrize("kind", ["library", "recent"])
@pytest.mark.parametrize("with_control", [False, True])
def test_closed_owner_refuses_refresh_before_service_or_runner_access(kind, with_control):
    # No runner, service, GTK widget tree, or pending attributes: invoking
    # the actual adapter must leave the detached owner entirely untouched.
    host = NS(_closed=True)
    control = object() if with_control else None
    if kind == "library":
        result = library_refresh.refresh_library(
            host, scan=True, control=control, refresh_covers=True,
        )
    else:
        result = library_refresh.refresh_recent(host, control=control)
    assert result is None
    assert vars(host) == {"_closed": True}



@pytest.mark.parametrize("completion", ["ok", "err"])
def test_automatic_library_invalidation_is_not_lost_while_refresh_pending(completion):
    host = _harness()
    host.refresh_library(False)
    stale = host.runner.requests[-1]
    assert len(host.runner.requests) == 1
    library_refresh.invalidate_library(host)  # indexed Numilog book
    library_refresh.invalidate_library(host)  # second source, same GTK turn
    assert len(host.runner.requests) == 1
    assert host._library_refresh_dirty is True
    if completion == "ok":
        stale.ok([{"identity_id": "alice", "stale": True}])
    else:
        stale.err(RuntimeError("an old read failed"))
    # Never display stale books or a redundant old-error toast.
    assert host.library_shown == []
    assert len(host.runner.requests) == 2
    assert host._library_refresh_dirty is False
    refreshed = host.runner.requests[-1]
    assert refreshed.work() == [{"identity_id": "alice"}]
    refreshed.ok(refreshed.work())
    assert host.library_shown == [[{"identity_id": "alice"}]]
    assert host._library_refresh_pending is False
    assert len(host.runner.requests) == 2


def test_pending_library_invalidation_refetches_only_current_identity():
    host = _harness()
    host.refresh_library(False)
    earlier = host.runner.requests[-1]
    library_refresh.invalidate_library(host)
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    earlier.ok([{"identity_id": "alice", "stale": True}])
    assert host.library_shown == []
    newer = host.runner.requests[-1]
    assert newer.work() == [{"identity_id": "bob"}]
    newer.ok(newer.work())
    assert host.library_shown == [[{"identity_id": "bob"}]]



def test_ordinary_background_refresh_never_supersedes_newer_manual_refresh():
    host = _harness()
    host.refresh_library(False)
    old = host.runner.requests[-1]
    host.refresh_library(False, control=object())
    manual = host.runner.requests[-1]
    assert len(host.runner.requests) == 2
    old.ok([{"title": "old"}])
    host.refresh_library(False)  # ordinary automatic refresh, not invalidation
    assert getattr(host, "_library_refresh_dirty", False) is False
    manual.ok([{"title": "manual latest"}])
    assert host.library_shown == [[{"title": "manual latest"}]]
    assert len(host.runner.requests) == 2


def test_new_local_artifact_during_inflight_refresh_gets_fresh_snapshot_once():
    """A completed ACSM/indexing task must not lose its no-scan GUI refresh."""
    host = _harness()
    documents = [{"key": "existing", "identity_id": "alice"}]
    calls = []

    def entries(*, identity_id, scan):
        calls.append((identity_id, scan))
        return [dict(row) for row in documents]

    host.service.library_entries = entries
    host.refresh_library(False)
    in_flight = host.runner.requests[-1]
    stale = in_flight.work()
    assert [r["key"] for r in stale] == ["existing"]

    documents.append({"key": "new-acsm-book", "identity_id": "alice"})
    # The indexed book is now committed; several observers request refresh.
    host.refresh_library(False)
    host.refresh_library(False)
    assert len(host.runner.requests) == 1

    in_flight.ok(stale)
    assert host.library_shown == []  # old snapshot must not overwrite new state
    assert len(host.runner.requests) == 2
    fresh = host.runner.requests[-1]
    latest = fresh.work()
    assert [r["key"] for r in latest] == ["existing", "new-acsm-book"]
    fresh.ok(latest)
    assert host.library_shown == [latest]
    assert calls == [("alice", False), ("alice", False)]
    assert host._library_refresh_pending is False


def test_coalesced_library_refresh_retries_after_obsolete_error():
    host = _harness()
    host.refresh_library(False)
    old = host.runner.requests[-1]
    host.refresh_library(False)
    old.err(RuntimeError("PRIVATE_OLD_SNAPSHOT_ERROR"))
    assert len(host.runner.requests) == 2
    assert host.library_shown == []
    fresh = host.runner.requests[-1]
    fresh.ok(fresh.work())
    assert host.library_shown == [[{"identity_id": "alice"}]]
    assert host._library_refresh_pending is False


def test_explicit_refresh_supersedes_background_followup_without_third_fetch():
    host = _harness()
    host.refresh_library(False)
    background = host.runner.requests[-1]
    host.refresh_library(False)  # pending follow-up
    host.refresh_library(True, control=object())  # now the authoritative work
    assert len(host.runner.requests) == 2
    manual = host.runner.requests[-1]
    assert background.work() == []  # superseded before any source IO
    background.ok([])
    manual.ok(manual.work())
    assert host.library_shown == [[{"identity_id": "alice"}]]
    assert len(host.runner.requests) == 2
    assert host._library_refresh_pending is False

